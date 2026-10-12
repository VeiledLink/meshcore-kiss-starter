#!/bin/bash
# Patch meshcore-pi (https://github.com/brianwiddas/meshcore-pi) with
# KISS modem support and some crash/compatibility fixes. Safe to re-run, e.g.
# after updating meshcore-pi. Backs up the files it changes the first time.
#
#   ./patch_meshcore_pi.sh                 # patches ~/meshcore-pi
#   MCP_DIR=/path/to/meshcore-pi ./patch_meshcore_pi.sh
#
# What it changes:
#  1. A stored route longer than a packet can carry no longer crashes
#     meshcore-pi; the DM is flooded instead (like a radio with no route).
#  2. Companion "add contact" for a contact it hasn't heard an advert from
#     answers OK instead of "not found" (RemoteTerm otherwise retries forever).
#  3. No outgoing packet can crash it on a too-long route (floods instead).
#  4. CMD_SEND_BINARY_REQ (command 50), used by RemoteTerm for repeater
#     status/telemetry, is supported.
#  5. A short contact-update payload can't crash the companion process.
#  6. New interface type "kiss": a radio running MeshCore's official
#     kiss_modem firmware (kissinterface.py, next to this script).
#  7. ACKs longer than 4 bytes (current firmware) are accepted, so DMs stop
#     looking undelivered.
#  8. DM routes are read and reported in MeshCore's real format (2/3-byte hop
#     codes, the 64-byte route field, 255 = no route). Before this, nearly
#     every saved route was garbage, so delivery confirmations (ACKs) went
#     nowhere and phones showed "Failed" for messages that did arrive.
set -euo pipefail

MCP_DIR="${MCP_DIR:-$HOME/meshcore-pi}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$MCP_DIR"
[[ -e packet.py.pre-floodfix ]] || cp -p packet.py packet.py.pre-floodfix
[[ -e companionradio.py.pre-addfix ]] || cp -p companionradio.py companionradio.py.pre-addfix

python3 - <<'PYEOF'
import pathlib, sys

def patch(filename, marker, old, new):
    p = pathlib.Path(filename)
    s = p.read_text()
    if marker in s:
        print(f"{filename} already patched")
        return
    if s.count(old) != 1:
        sys.exit(f"{filename} doesn't look as expected - not patching. Your meshcore-pi version may differ - check it by hand.")
    p.write_text(s.replace(old, new))
    print(f"Patched {filename}")

patch("packet.py", "Fall back to flooding",
"""        path = dest.path

        super().__init__(type, path)""",
"""        path = dest.path
        # A stored route longer than a packet can carry (e.g. learned from a
        # far-travelled flood advert) used to raise "Path is too long" and
        # crash meshcore-pi. Fall back to flooding, like a radio with no route.
        if path is not None and len(path) > 63:
            path = None

        super().__init__(type, path)""")

patch("companionradio.py", "ignoring add",
"""            logger.error(f"Contact {hexlify(pubkey).decode()} not found")
            return ERR(ERR_CODE_NOT_FOUND)""",
"""            # meshcore-pi can only store contacts it heard an advert from. Answering
            # "not found" made RemoteTerm retry every such contact non-stop, so say
            # OK and ignore it - the contact appears once its advert is heard.
            logger.debug(f"Contact {hexlify(pubkey).decode()} not known yet - ignoring add")
            return OK""")

patch("packet.py", "too long for a packet",
"""            else:
                raise ValueError("Path must be bytes or bytearray")
            if len(path) > 63:
                raise ValueError("Path is too long")""",
"""            else:
                raise ValueError("Path must be bytes or bytearray")
            if len(path) > 63:
                # A route too long for a packet used to raise here and crash
                # meshcore-pi (e.g. on a repeater login). Flood instead.
                self.path = bytearray()
                self.route = self.ROUTE_FLOOD""")
# 4. CMD_SEND_BINARY_REQ (command 50)
patch("companionradio.py", "CMD_SEND_BINARY_REQ = 50",
"CMD_GET_ADVERT_PATH = 42",
"CMD_GET_ADVERT_PATH = 42\nCMD_SEND_BINARY_REQ = 50")

patch("companionradio.py", "PUSH_CODE_BINARY_RESPONSE = 0x8C",
"PUSH_CODE_TELEMETRY_RESPONSE = 0x8B",
"PUSH_CODE_TELEMETRY_RESPONSE = 0x8B\nPUSH_CODE_BINARY_RESPONSE = 0x8C")

patch("companionradio.py", "self.pending_binary = {}",
"""        self.pending_status = None      # Target we sent a status request to""",
"""        self.pending_status = None      # Target we sent a status request to
        self.pending_binary = {}        # tag (4 bytes) -> (target pubkey, sent time) for CMD_SEND_BINARY_REQ""")

patch("companionradio.py", "async def send_binary_req",
"""    async def rx_response(self, packet:packet.MC_SrcDest):""",
"""    # Binary request (CMD_SEND_BINARY_REQ): the app supplies the request type
    # and data. Newer clients (e.g. RemoteTerm's repeater telemetry) use this
    # instead of CMD_SEND_STATUS_REQ. The tag is the request timestamp, which
    # the remote end reflects in the first 4 bytes of its response.
    async def send_binary_req(self, pubkey, request_type, data):
        dest = self.ids.find_by_pubkey(pubkey)
        if dest is None:
            return ERR(ERR_CODE_NOT_FOUND)

        req = packet.MC_Req_Out(self.me, dest, request_type, data)
        tag = struct.pack("<L", req.timestamp)

        now = time.time()
        self.pending_binary = {t: v for t, v in self.pending_binary.items() if now - v[1] < 120}
        self.pending_binary[tag] = (dest.pubkey, now)

        await self.transmit_packet(req)
        return sent_resp(req, tag)

    async def rx_response(self, packet:packet.MC_SrcDest):""")

patch("companionradio.py", "Sending PUSH_CODE_BINARY_RESPONSE",
"""        logger.debug(f"Got RESPONSE frame: {hexlify(frame).decode()}, timestamp: {timestamp} ({time.ctime(timestamp)})")
        if self.pending_login""",
"""        logger.debug(f"Got RESPONSE frame: {hexlify(frame).decode()}, timestamp: {timestamp} ({time.ctime(timestamp)})")
        pending = self.pending_binary.get(frame[0:4])
        if pending and pending[0] == packet.source.pubkey:
            del self.pending_binary[frame[0:4]]
            # Send to client
            #   PUSH_CODE_BINARY_RESPONSE
            #   0 (reserved)
            #   tag, 4 bytes
            #   response data (everything after the reflected tag)
            msg = bytes([PUSH_CODE_BINARY_RESPONSE, 0]) + frame[0:4] + frame[4:]
            logger.info(f"Sending PUSH_CODE_BINARY_RESPONSE to app, tag {hexlify(frame[0:4]).decode()}")
            await self.appinterface.tx(msg)

        elif self.pending_login""")

patch("companionradio.py", "command == CMD_SEND_BINARY_REQ",
"""            elif command == CMD_SEND_TRACE_PATH:""",
"""            elif command == CMD_SEND_BINARY_REQ:
                # Binary request to remote device
                # - device public key (32 bytes)
                # - request type (1 byte)
                # - request data (optional)
                if len(frame) < 34:
                    response = ERR(ERR_CODE_ILLEGAL_ARG)
                else:
                    pubkey = frame[1:33]
                    request_type = frame[33]
                    logger.debug(f"CMD_SEND_BINARY_REQ, to: {hexlify(pubkey).decode()}, type: {request_type}")
                    response = await self.send_binary_req(pubkey, request_type, bytes(frame[34:]))

            elif command == CMD_SEND_TRACE_PATH:""")
# 5. short contact-update payloads
patch("companionradio.py", "crashing the whole companion radio process on a short buffer",
"""        last_advert_time = struct.unpack("<L", rest[32:36])[0]""",
"""
        # last_advert_time is not always present (some contact update payloads
        # are shorter than the full struct) - default to 0 rather than
        # crashing the whole companion radio process on a short buffer.
        if len(rest) >= 36:
            last_advert_time = struct.unpack("<L", rest[32:36])[0]
        else:
            last_advert_time = 0""")

patch("packet.py", "ACKs can carry more than 4 bytes",
"""        # Ack hashes are 4 bytes
        if len(self._payload) != 4:
            raise InvalidMeshcorePacket("Ack packet payload is not 4 bytes")
        self.ackhash = bytes(self._payload)""",
"""        # Ack hashes are 4 bytes. ACKs can carry more than 4 bytes (current
        # MeshCore firmware); like the firmware, use the first 4.
        if len(self._payload) < 4:
            raise InvalidMeshcorePacket("Ack packet payload is shorter than 4 bytes")
        self.ackhash = bytes(self._payload[:4])""")

# 8. routes in MeshCore's real format
patch("packet.py", "the length byte became the first hop",
"""            pathlen = self.packetdata[0]
            # If pathlen = 0 (direct), pathdata = []
            self.pathdata = self.packetdata[0:pathlen]

            self.extra_type = None
            # Gather up anything left after the path
            extra = self.packetdata[pathlen+1:]""",
"""            # Path length byte (as in packet headers): low 6 bits = hops, top
            # 2 bits = bytes per hop - 1; the path follows it. (This used to
            # slice from byte 0, so the length byte became the first hop.)
            # Direct packets from meshcore-pi use 1-byte hop codes; a longer
            # code's first byte is its 1-byte code.
            meta = self.packetdata[0]
            size, count = (meta >> 6) + 1, meta & 0x3F
            raw = self.packetdata[1:1 + count * size]
            self.pathdata = bytearray(raw[i * size] for i in range(len(raw) // size))

            self.extra_type = None
            # Gather up anything left after the path
            extra = self.packetdata[1 + count * size:]""")

patch("companionradio.py", "route field is always 64 bytes",
"""        pathlen = contactdata[34]

        # If pathlen is 0 (ie, direct, zero-hop), path will be [] (as a bytes object, ie b'')
        path = contactdata[35:35+pathlen]

        rest = contactdata[35+pathlen:]""",
"""        pathlen = contactdata[34]

        # The route field is always 64 bytes, then the name. Length byte:
        # 255 = no route (flood); else low 6 bits = hops, top 2 bits = bytes
        # per hop - 1. Kept as 1-byte hop codes (what meshcore-pi sends with).
        size, count = (pathlen >> 6) + 1, pathlen & 0x3F
        if pathlen == 0xFF or size > 3 or count * size > 64:
            path = None
        else:
            raw = contactdata[35:35 + count * size]
            path = bytes(raw[i * size] for i in range(len(raw) // size))

        rest = contactdata[35 + 64:]""")

patch("companionradio.py", "No route is 255",
"""        if contact.path is None:
            path = bytes()
        else:
            path = contact.path
        contactresponse += struct.pack("<BBB", contact.advert.adv_type.value, contact.advert.adv_flags.value, len(path))""",
"""        # No route is 255 ("flood"), not 0 - 0 means a direct neighbour.
        if contact.path is None or len(contact.path) > 63:
            path, pathlen = bytes(), 0xFF
        else:
            path, pathlen = bytes(contact.path), len(contact.path)
        contactresponse += struct.pack("<BBB", contact.advert.adv_type.value, contact.advert.adv_flags.value, pathlen)""")

patch("interfaces/interface.py", 'interface_type == "kiss"',
"""        else:
            logger.error(f"Interface {i} is unknown type {interface_type}")""",
"""        elif interface_type == "kiss":
            try:
                from . import kissinterface
                i_face = kissinterface.KissInterface(data)

                interfaces.append(i_face)
            except Exception as e:
                logger.error(f"Unable to configure interface {i}: {repr(e)}")
                raise

        else:
            logger.error(f"Interface {i} is unknown type {interface_type}")""")
PYEOF

# 6. the KISS interface itself (kept next to this script)
cp "$HERE/kissinterface.py" interfaces/kissinterface.py

python3 -m py_compile packet.py companionradio.py interfaces/interface.py interfaces/kissinterface.py
git diff packet.py companionradio.py interfaces/interface.py > meshcore-starter-fixes.patch 2>/dev/null || true
echo "meshcore-pi patched. Restart it to use the changes (e.g. sudo systemctl restart meshcore-pi)."
