"""MeshCore KISS modem as a radio interface for meshcore-pi.

Talks to a radio running MeshCore's official kiss_modem firmware (e.g. the
RAK_4631_kiss_modem build) over USB serial. The modem is just a radio: it
sends the raw packets we give it (with its own listen-before-talk) and hands
back every packet it hears, followed by its SNR/RSSI. Identities and crypto
stay in meshcore-pi - the modem's own key and crypto commands aren't used.

The modem keeps no radio settings across a reboot, so they come from the
config and are sent every time we connect:

    [interface.kiss]
    type = "kiss"
    port = "/dev/serial/by-id/usb-RAKwireless_WisCore_RAK4631_Board_<serial>-if00"
    freq = 910.525      # MHz
    bw = 62.5           # kHz
    sf = 7
    cr = 5
    txpower = 22        # dBm

Copied into meshcore-pi/interfaces/ by patch_meshcore_pi.sh (this folder).
Protocol: MeshCore examples/kiss_modem/KissModem.h.
"""
import asyncio
import math
import struct
import time
from binascii import hexlify

import serial_asyncio
from aiotools import current_taskgroup

from configuration import ConfigView

from .interface import Interface

import logging
logger = logging.getLogger(__name__)

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
KISS_DATA, KISS_SETHARDWARE = 0x00, 0x06

HW_SET_RADIO = 0x09
HW_SET_TX_POWER = 0x0A
HW_GET_RADIO = 0x0B
HW_GET_TX_POWER = 0x0C
HW_GET_VERSION = 0x11
HW_SET_SIGNAL_REPORT = 0x19
HW_OK = 0xF0
HW_ERROR = 0xF1
HW_TX_DONE = 0xF8
HW_RX_META = 0xF9
HW_ERR_TX_BUSY = 0x07

MAX_PACKET = 255
REPLY_TIMEOUT = 2.0        # s, for setup commands
META_WAIT = 0.25           # s, how long a received packet waits for its SNR/RSSI frame
TX_DONE_MARGIN = 15.0      # s, on top of the estimated airtime: the modem waits for a clear channel
                           # first, which can take several seconds while repeaters relay a flood


def kiss_frame(kiss_type, payload=b""):
    body = bytes([kiss_type]) + payload
    body = body.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + body + bytes([FEND])


def hw_frame(sub_cmd, data=b""):
    return kiss_frame(KISS_SETHARDWARE, bytes([sub_cmd]) + data)


def lora_airtime_ms(length, sf, bw_hz, cr, preamble=16):
    """LoRa time on air (Semtech formula), explicit header, CRC on."""
    if not (sf and bw_hz):
        return 0
    t_sym = (2 ** sf) / bw_hz * 1000
    de = 1 if t_sym > 16 else 0     # low data rate optimisation
    payload_syms = 8 + max(math.ceil((8 * length - 4 * sf + 28 + 16) / (4 * (sf - 2 * de))) * cr, 0)
    return (preamble + 4.25) * t_sym + payload_syms * t_sym


class KissInterface(Interface):
    def __init__(self, config: ConfigView):
        super().__init__()
        self._name = "KISS modem interface"

        self._port = config.get("port")
        if not self._port:
            raise ValueError("No serial port specified for KISS interface")

        self.freq = float(config.get("freq", 910.525))     # MHz
        self.bw = float(config.get("bw", 62.5))            # kHz
        self.sf = int(config.get("sf", 7))
        self.cr = int(config.get("cr", 5))
        self.txpower = int(config.get("txpower", 22))
        self.txmaxpower = int(config.get("txmaxpower", 22))

        self._reader = None
        self._writer = None
        self._connected = False
        self._replies = asyncio.Queue()      # hardware replies to our setup commands
        self._tx_done = None                 # Future set by the modem's TX_DONE
        self._tx_lock = asyncio.Lock()       # the modem holds one packet at a time
        self._pending_rx = None              # (packet, time) waiting for its RX_META

    # ---- framing ---------------------------------------------------------

    async def _write(self, frame):
        if self._writer is None:
            raise ConnectionError("KISS modem not connected")
        self._writer.write(frame)
        await self._writer.drain()

    async def _read_frame(self):
        """Next complete KISS frame (unescaped, type byte first)."""
        buf, escaped = bytearray(), False
        while True:
            b = (await self._reader.readexactly(1))[0]
            if b == FEND:
                if buf:
                    return bytes(buf)
                continue            # empty frame / frame start
            if escaped:
                buf.append(FEND if b == TFEND else FESC if b == TFESC else b)
                escaped = False
            elif b == FESC:
                escaped = True
            else:
                buf.append(b)
            if len(buf) > 600:
                logger.warning("Oversized KISS frame from modem, discarding")
                buf.clear()

    async def _command(self, sub_cmd, data=b"", expect=None):
        """Send a hardware command and return its reply payload (expect = reply code)."""
        while not self._replies.empty():
            self._replies.get_nowait()
        await self._write(hw_frame(sub_cmd, data))
        while True:
            code, payload = await asyncio.wait_for(self._replies.get(), REPLY_TIMEOUT)
            if code == HW_ERROR:
                raise RuntimeError(f"modem error {payload.hex()} for command 0x{sub_cmd:02x}")
            if expect is None or code == expect:
                return payload

    # ---- receive ---------------------------------------------------------

    async def _flush_pending_rx(self, rssi=0, snr=0.0):
        if self._pending_rx is not None:
            packet, _ = self._pending_rx
            self._pending_rx = None
            await self.rx_q.put((packet, rssi, snr))

    async def _frame_pump(self, frames):
        # Only this task reads the serial port, so a timeout elsewhere can
        # never cut a frame in half. Ends (with None) when the link fails.
        try:
            while True:
                await frames.put(await self._read_frame())
        finally:
            frames.put_nowait(None)

    async def _reader_loop(self):
        frames = asyncio.Queue()
        pump = asyncio.ensure_future(self._frame_pump(frames))
        try:
            await self._handle_frames(frames)
        finally:
            pump.cancel()
        if pump.done() and not pump.cancelled() and pump.exception():
            raise pump.exception()

    async def _handle_frames(self, frames):
        while True:
            try:
                frame = await asyncio.wait_for(frames.get(), META_WAIT if self._pending_rx else None)
            except asyncio.TimeoutError:
                await self._flush_pending_rx()        # no signal report came: pass it on without
                continue
            if frame is None:
                await self._flush_pending_rx()
                raise ConnectionError("KISS modem serial link closed")

            kiss_type = frame[0]
            if kiss_type == KISS_DATA:
                await self._flush_pending_rx()
                packet = frame[1:]
                if packet:
                    self._pending_rx = (packet, time.time())
                continue

            if kiss_type != KISS_SETHARDWARE or len(frame) < 2:
                continue
            code, payload = frame[1], frame[2:]

            if code == HW_RX_META and len(payload) >= 2:
                snr, rssi = struct.unpack("<bb", payload[:2])
                await self._flush_pending_rx(rssi=rssi, snr=snr / 4)
            elif code == HW_TX_DONE:
                if self._tx_done is not None and not self._tx_done.done():
                    self._tx_done.set_result(bool(payload and payload[0] == 1))
            elif code == HW_ERROR and payload and payload[0] == HW_ERR_TX_BUSY and self._tx_done is not None:
                if not self._tx_done.done():
                    self._tx_done.set_result(False)
            else:
                await self._replies.put((code, payload))

    # ---- connection ------------------------------------------------------

    async def _setup(self):
        version = await self._command(HW_GET_VERSION, expect=0x80 | HW_GET_VERSION)
        await self._command(HW_SET_RADIO, struct.pack("<IIBB", round(self.freq * 1e6), round(self.bw * 1e3),
                                                      self.sf, self.cr), expect=HW_OK)
        await self._command(HW_SET_TX_POWER, bytes([self.txpower]), expect=HW_OK)
        await self._command(HW_SET_SIGNAL_REPORT, b"\x01", expect=0x80 | 0x1A)

        # Read back what the modem is actually using
        radio = await self._command(HW_GET_RADIO, expect=0x80 | HW_GET_RADIO)
        freq_hz, bw_hz = struct.unpack("<II", radio[:8])
        power = (await self._command(HW_GET_TX_POWER, expect=0x80 | HW_GET_TX_POWER))[0]
        if (freq_hz, bw_hz, radio[8], radio[9], power) != (round(self.freq * 1e6), round(self.bw * 1e3),
                                                             self.sf, self.cr, self.txpower):
            raise RuntimeError(f"modem didn't take the radio settings (reads {freq_hz} Hz, {bw_hz} Hz, "
                               f"SF{radio[8]}, CR{radio[9]}, {power} dBm)")
        logger.info(f"KISS modem connected on {self._port}: firmware v{version[0]}, {self.freq} MHz, "
                    f"{self.bw} kHz, SF{self.sf}, CR{self.cr}, {self.txpower} dBm")

    async def _run_once(self):
        self._reader, self._writer = await serial_asyncio.open_serial_connection(url=self._port, baudrate=115200)
        reader_task = asyncio.ensure_future(self._reader_loop())
        try:
            await self._setup()
            self._connected = True
            await reader_task                 # runs until the serial link fails
        finally:
            self._connected = False
            reader_task.cancel()
            if self._tx_done is not None and not self._tx_done.done():
                self._tx_done.set_result(False)
            try:
                self._writer.close()
            except Exception:
                pass
            self._reader = self._writer = None

    async def radiointerface(self):
        # Reconnect after any failure (unplugged, modem reboot, ...); the
        # settings are re-sent on every connect, so a rebooted modem is fixed.
        delay = 1
        while True:
            start = time.time()
            try:
                await self._run_once()
            except Exception as e:
                logger.error(f"KISS modem interface error: {repr(e)}")
            delay = 1 if time.time() - start > 30 else min(delay * 2, 60)
            logger.info(f"KISS modem disconnected, retrying in {delay} seconds")
            await asyncio.sleep(delay)

    # ---- Interface API ---------------------------------------------------

    async def transmit(self, tx_packet):
        """Send one packet; returns its airtime in ms (0 if it wasn't sent)."""
        if not self._connected:
            logger.warning("Cannot send, KISS modem not connected")
            return 0
        if len(tx_packet) > MAX_PACKET:
            logger.error(f"Packet too long for the modem ({len(tx_packet)} bytes), not sent")
            return 0
        airtime = lora_airtime_ms(len(tx_packet), self.sf, self.bw * 1000, self.cr)
        async with self._tx_lock:
            self._tx_done = asyncio.get_running_loop().create_future()
            try:
                await self._write(kiss_frame(KISS_DATA, bytes(tx_packet)))
                ok = await asyncio.wait_for(self._tx_done, airtime / 1000 * 1.5 + TX_DONE_MARGIN)
            except Exception as e:
                logger.warning(f"KISS modem transmit failed: {repr(e)}")
                return 0
            finally:
                self._tx_done = None
        if not ok:
            logger.warning(f"KISS modem reported the transmit failed: {hexlify(tx_packet[:8]).decode()}...")
            return 0
        return airtime

    def transmit_wait(self):
        # The modem does listen-before-talk itself; no duty cycle limit at 915 MHz.
        return 0

    # (frequency kHz, bandwidth Hz, SF, CR, tx power dBm, max tx power dBm),
    # the same units the companion interface reports
    def get_radioconfig(self):
        return (round(self.freq * 1000), round(self.bw * 1000), self.sf, self.cr, self.txpower, self.txmaxpower)

    async def start(self):
        current_taskgroup.get().create_task(self.radiointerface(), name="KISS modem interface")
