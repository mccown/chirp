# -*- coding: utf-8 -*-
# Copyright 2013 Andrew Morgan <ziltro@ziltro.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import time
import os
import struct
import unittest

from chirp import chirp_common, directory, memmap
from chirp import bitwise, errors, util
from chirp.settings import RadioSetting, RadioSettingGroup, \
    RadioSettingValueInteger, RadioSettingValueList, \
    RadioSettingValueBoolean

DEBUG = os.getenv("CHIRP_DEBUG") and True or False

MEM_FORMAT = """
#seekto 0x0010;
struct {
    lbcd rxfreq[4];
    lbcd txfreq[4];
    lbcd rxtone[2];
    lbcd txtone[2];
    u8 unknown3:1,
       unknown2:1,
       unknown1:1,
       skip:1,
       highpower:1,
       narrow:1,
       beatshift:1,
       bcl:1;
    u8 unknown4[3];
} memory[16];
#seekto 0x02B0;
struct {
    u8 voiceprompt;
    u8 voicelanguage;
    u8 scan;
    u8 vox;
    u8 voxlevel;
    u8 voxinhibitonrx;
    u8 lowvolinhibittx;
    u8 highvolinhibittx;
    u8 alarm;
    u8 fmradio;
} settings;
#seekto 0x03C0;
struct {
    u8 unused:6,
       batterysaver:1,
       beep:1;
    u8 squelchlevel;
    u8 sidekeyfunction;
    u8 timeouttimer;
    u8 unused2[3];
    u8 unused3:7,
       scanmode:1;
} settings2;
"""

CMD_ACK = "\x06"
BLOCK_SIZE = 0x08
UPLOAD_BLOCKS = [range(0x0000, 0x0110, 8),
                 range(0x02b0, 0x02c0, 8),
                 range(0x0380, 0x03e0, 8)]

# TODO: Is it 1 watt?
H777_POWER_LEVELS = [chirp_common.PowerLevel("Low", watts=1.00),
                     chirp_common.PowerLevel("High", watts=5.00)]
VOICE_LIST = ["English", "Chinese"]
SIDEKEYFUNCTION_LIST = ["Off", "Monitor", "Transmit Power", "Alarm"]
TIMEOUTTIMER_LIST = ["Off", "30 seconds", "60 seconds", "90 seconds",
                     "120 seconds", "150 seconds", "180 seconds",
                     "210 seconds", "240 seconds", "270 seconds",
                     "300 seconds"]
SCANMODE_LIST = ["Carrier", "Time"]

SETTING_LISTS = {
    "voice" : VOICE_LIST,
    }

def _h777_enter_programming_mode(radio):
    serial = radio.pipe

    try:
        serial.write("\x02")
        time.sleep(0.1)
        serial.write("PROGRAM")
        ack = serial.read(1)
    except:
        raise errors.RadioError("Error communicating with radio")

    if not ack:
        raise errors.RadioError("No response from radio")
    elif ack != CMD_ACK:
        raise errors.RadioError("Radio refused to enter programming mode")

    try:
        serial.write("\x02")
        ident = serial.read(8)
    except:
        raise errors.RadioError("Error communicating with radio")

    if not ident.startswith("P3107"):
        print util.hexprint(ident)
        raise errors.RadioError("Radio returned unknown identification string")

    try:
        serial.write(CMD_ACK)
        ack = serial.read(1)
    except:
        raise errors.RadioError("Error communicating with radio")

    if ack != CMD_ACK:
        raise errors.RadioError("Radio refused to enter programming mode")

def _h777_exit_programming_mode(radio):
    serial = radio.pipe
    try:
        serial.write("E")
    except:
        raise errors.RadioError("Radio refused to exit programming mode")

def _h777_read_block(radio, block_addr, block_size):
    serial = radio.pipe

    cmd = struct.pack(">cHb", 'R', block_addr, BLOCK_SIZE)
    expectedresponse = "W" + cmd[1:]
    if DEBUG:
        print("Reading block %04x..." % (block_addr))

    try:
        serial.write(cmd)
        response = serial.read(4 + BLOCK_SIZE)
        if response[:4] != expectedresponse:
            raise Exception("Error reading block %04x." % (block_addr))

        block_data = response[4:]

        serial.write(CMD_ACK)
        ack = serial.read(1)
    except:
        raise errors.RadioError("Failed to read block at %04x" % block_addr)

    if ack != CMD_ACK:
        raise Exception("No ACK reading block %04x." % (block_addr))

    return block_data

def _h777_write_block(radio, block_addr, block_size):
    serial = radio.pipe

    cmd = struct.pack(">cHb", 'W', block_addr, BLOCK_SIZE)
    data = radio.get_mmap()[block_addr:block_addr + 8]

    if DEBUG:
        print("Writing Data:")
        print util.hexprint(cmd + data)

    try:
        serial.write(cmd + data)
        if serial.read(1) != CMD_ACK:
            raise Exception("No ACK")
    except:
        raise errors.RadioError("Failed to send block "
                                "to radio at %04x" % block_addr)

def do_download(radio):
    print "download"
    _h777_enter_programming_mode(radio)

    data = ""

    status = chirp_common.Status()
    status.msg = "Cloning from radio"

    status.cur = 0
    status.max = radio._memsize

    for addr in range(0, radio._memsize, BLOCK_SIZE):
        status.cur = addr + BLOCK_SIZE
        radio.status_fn(status)

        block = _h777_read_block(radio, addr, BLOCK_SIZE)
        data += block

        if DEBUG:
            print "Address: %04x" % addr
            print util.hexprint(block)

    _h777_exit_programming_mode(radio)

    return memmap.MemoryMap(data)

def do_upload(radio):
    status = chirp_common.Status()
    status.msg = "Uploading to radio"

    _h777_enter_programming_mode(radio)

    status.cur = 0
    status.max = radio._memsize

    for start_addr, end_addr in radio._ranges:
        for addr in range(start_addr, end_addr, BLOCK_SIZE):
            status.cur = addr + BLOCK_SIZE
            radio.status_fn(status)
            _h777_write_block(radio, addr, BLOCK_SIZE)

    _h777_exit_programming_mode(radio)

@directory.register
class H777Radio(chirp_common.CloneModeRadio):
    """HST H-777"""
    # VENDOR = "Heng Shun Tong (恒顺通)"
    # MODEL = "H-777"
    VENDOR = "Baofeng"
    MODEL = "BF-888"
    BAUD_RATE = 9600

    # This code currently requires that ranges start at 0x0000
    # and are continious. In the original program 0x0388 and 0x03C8
    # are only written (all bytes 0xFF), not read.
    #_ranges = [
    #       (0x0000, 0x0110),
    #       (0x02B0, 0x02C0),
    #       (0x0380, 0x03E0)
    #       ]
    # Memory starts looping at 0x1000... But not every 0x1000.

    _ranges = [
        (0x0000, 0x0110),
        (0x02B0, 0x02C0),
        (0x0380, 0x03E0),
        ]
    _memsize = 0x03E0

    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_settings = True
        rf.valid_modes = ["NFM", "FM"]  # 12.5 KHz, 25 kHz.
        rf.valid_skips = ["", "S"]
        rf.valid_tmodes = ["", "Tone", "TSQL", "DTCS", "Cross"]
        rf.has_rx_dtcs = True
        rf.has_ctone = True
        rf.has_cross = True
        rf.has_tuning_step = False
        rf.has_bank = False
        rf.has_name = False
        rf.memory_bounds = (1, 16)
        rf.valid_bands = [(400000000, 470000000)]
        rf.valid_power_levels = H777_POWER_LEVELS

        return rf

    def process_mmap(self):
        self._memobj = bitwise.parse(MEM_FORMAT, self._mmap)

    def sync_in(self):
        self._mmap = do_download(self)
        self.process_mmap()

    def sync_out(self):
        do_upload(self)

    def get_raw_memory(self, number):
        return repr(self._memobj.memory[number - 1])

    def _decode_tone(self, val):
        val = int(val)
        if val == 16665:
            return '', None, None
        elif val >= 12000:
            return 'DTCS', val - 12000, 'R'
        elif val >= 8000:
            return 'DTCS', val - 8000, 'N'
        else:
            return 'Tone', val / 10.0, None

    def _encode_tone(self, memval, mode, value, pol):
        if mode == '':
            memval[0].set_raw(0xFF)
            memval[1].set_raw(0xFF)
        elif mode == 'Tone':
            memval.set_value(int(value * 10))
        elif mode == 'DTCS':
            flag = 0x80 if pol == 'N' else 0xC0
            memval.set_value(value)
            memval[1].set_bits(flag)
        else:
            raise Exception("Internal error: invalid mode `%s'" % mode)

    def get_memory(self, number):
        _mem = self._memobj.memory[number - 1]

        mem = chirp_common.Memory()

        mem.number = number
        mem.freq = int(_mem.rxfreq) * 10

        # We'll consider any blank (i.e. 0MHz frequency) to be empty
        if mem.freq == 0:
            mem.empty = True
            return mem

        if _mem.rxfreq.get_raw() == "\xFF\xFF\xFF\xFF":
            mem.freq = 0
            mem.empty = True
            return mem

        if int(_mem.rxfreq) == int(_mem.txfreq):
            mem.duplex = ""
            mem.offset = 0
        else:
            mem.duplex = int(_mem.rxfreq) > int(_mem.txfreq) and "-" or "+"
            mem.offset = abs(int(_mem.rxfreq) - int(_mem.txfreq)) * 10

        mem.mode = not _mem.narrow and "FM" or "NFM"
        mem.power = H777_POWER_LEVELS[_mem.highpower]

        mem.skip = _mem.skip and "S" or ""

        txtone = self._decode_tone(_mem.txtone)
        rxtone = self._decode_tone(_mem.rxtone)
        chirp_common.split_tone_decode(mem, txtone, rxtone)

        mem.extra = RadioSettingGroup("Extra", "extra")
        rs = RadioSetting("bcl", "Busy Channel Lockout",
                          RadioSettingValueBoolean(not _mem.bcl))
        mem.extra.append(rs)
        rs = RadioSetting("beatshift", "Beat Shift(scramble)",
                          RadioSettingValueBoolean(not _mem.beatshift))
        mem.extra.append(rs)

        return mem

    def set_memory(self, mem):
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[mem.number - 1]

        if mem.empty:
            _mem.set_raw("\xFF" * (_mem.size() / 8))
            return

        _mem.rxfreq = mem.freq / 10

        if mem.duplex == "off":
            for i in range(0, 4):
                _mem.txfreq[i].set_raw("\xFF")
        elif mem.duplex == "split":
            _mem.txfreq = mem.offset / 10
        elif mem.duplex == "+":
            _mem.txfreq = (mem.freq + mem.offset) / 10
        elif mem.duplex == "-":
            _mem.txfreq = (mem.freq - mem.offset) / 10
        else:
            _mem.txfreq = mem.freq / 10

        txtone, rxtone = chirp_common.split_tone_encode(mem)
        self._encode_tone(_mem.txtone, *txtone)
        self._encode_tone(_mem.rxtone, *rxtone)

        _mem.narrow = 'N' in mem.mode
        _mem.highpower = mem.power == H777_POWER_LEVELS[1]
        _mem.skip = mem.skip == "S"

        for setting in mem.extra:
            # NOTE: Only two settings right now, both are inverted
            setattr(_mem, setting.get_name(), not setting.value)

    def get_settings(self):
        _settings = self._memobj.settings
        basic = RadioSettingGroup("basic", "Basic Settings")

        # TODO: Check that all these settings actually do what they
        # say they do.

        rs = RadioSetting("voiceprompt", "Voice prompt",
                          RadioSettingValueBoolean(_settings.voiceprompt))
        basic.append(rs)

        rs = RadioSetting("voicelanguage", "Voice language",
                          RadioSettingValueList(VOICE_LIST,
                              VOICE_LIST[_settings.voicelanguage]))
        basic.append(rs)

        rs = RadioSetting("scan", "Scan",
                          RadioSettingValueBoolean(_settings.scan))
        basic.append(rs)

        rs = RadioSetting("settings2.scanmode", "Scan mode",
                          RadioSettingValueList(SCANMODE_LIST,
                          SCANMODE_LIST[self._memobj.settings2.scanmode]))
        basic.append(rs)

        rs = RadioSetting("vox", "VOX",
                          RadioSettingValueBoolean(_settings.vox))
        basic.append(rs)

        rs = RadioSetting("voxlevel", "VOX level",
                          RadioSettingValueInteger(
                              1, 5, _settings.voxlevel + 1))
        basic.append(rs)

        rs = RadioSetting("voxinhibitonrx", "Inhibit VOX on receive",
                          RadioSettingValueBoolean(_settings.voxinhibitonrx))
        basic.append(rs)

        rs = RadioSetting("lowvolinhibittx", "Low voltage inhibit transmit",
                          RadioSettingValueBoolean(_settings.lowvolinhibittx))
        basic.append(rs)

        rs = RadioSetting("highvolinhibittx", "High voltage inhibit transmit",
                          RadioSettingValueBoolean(_settings.highvolinhibittx))
        basic.append(rs)

        rs = RadioSetting("alarm", "Alarm",
                          RadioSettingValueBoolean(_settings.alarm))
        basic.append(rs)

        # TODO: This should probably be called “FM Broadcast Band Radio”
        # or something. I'm not sure if the model actually has one though.
        rs = RadioSetting("fmradio", "FM function",
                          RadioSettingValueBoolean(_settings.fmradio))
        basic.append(rs)

        rs = RadioSetting("settings2.beep", "Beep",
                          RadioSettingValueBoolean(
                              self._memobj.settings2.beep))
        basic.append(rs)

        rs = RadioSetting("settings2.batterysaver", "Battery saver",
                          RadioSettingValueBoolean(
                              self._memobj.settings2.batterysaver))
        basic.append(rs)

        rs = RadioSetting("settings2.squelchlevel", "Squelch level",
                          RadioSettingValueInteger(0, 9,
                              self._memobj.settings2.squelchlevel))
        basic.append(rs)

        rs = RadioSetting("settings2.sidekeyfunction", "Side key function",
                          RadioSettingValueList(SIDEKEYFUNCTION_LIST,
                          SIDEKEYFUNCTION_LIST[
                              self._memobj.settings2.sidekeyfunction]))
        basic.append(rs)

        rs = RadioSetting("settings2.timeouttimer", "Timeout timer",
                          RadioSettingValueList(TIMEOUTTIMER_LIST,
                          TIMEOUTTIMER_LIST[
                              self._memobj.settings2.timeouttimer]))
        basic.append(rs)

        return basic

    def set_settings(self, settings):
        for element in settings:
            if not isinstance(element, RadioSetting):
                self.set_settings(element)
                continue
            else:
                try:
                    if "." in element.get_name():
                        bits = element.get_name().split(".")
                        obj = self._memobj
                        for bit in bits[:-1]:
                            obj = getattr(obj, bit)
                        setting = bits[-1]
                    else:
                        obj = self._memobj.settings
                        setting = element.get_name()

                    if element.has_apply_callback():
                        print "Using apply callback"
                        element.run_apply_callback()
                    elif setting == "voxlevel":
                        setattr(obj, setting, int(element.value) - 1)
                    else:
                        print "Setting %s = %s" % (setting, element.value)
                        setattr(obj, setting, element.value)
                except Exception, e:
                    print element.get_name()
                    raise

class H777TestCase(unittest.TestCase):
    def setUp(self):
        self.driver = H777Radio(None)
        self.testdata = bitwise.parse("lbcd foo[2];",
                                      memmap.MemoryMap("\x00\x00"))

    def test_decode_tone_dtcs_normal(self):
        mode, value, pol = self.driver._decode_tone(8023)
        self.assertEqual('DTCS', mode)
        self.assertEqual(23, value)
        self.assertEqual('N', pol)

    def test_decode_tone_dtcs_rev(self):
        mode, value, pol = self.driver._decode_tone(12023)
        self.assertEqual('DTCS', mode)
        self.assertEqual(23, value)
        self.assertEqual('R', pol)

    def test_decode_tone_tone(self):
        mode, value, pol = self.driver._decode_tone(885)
        self.assertEqual('Tone', mode)
        self.assertEqual(88.5, value)
        self.assertEqual(None, pol)

    def test_decode_tone_none(self):
        mode, value, pol = self.driver._decode_tone(16665)
        self.assertEqual('', mode)
        self.assertEqual(None, value)
        self.assertEqual(None, pol)

    def test_encode_tone_dtcs_normal(self):
        self.driver._encode_tone(self.testdata.foo, 'DTCS', 23, 'N')
        self.assertEqual(8023, int(self.testdata.foo))

    def test_encode_tone_dtcs_rev(self):
        self.driver._encode_tone(self.testdata.foo, 'DTCS', 23, 'R')
        self.assertEqual(12023, int(self.testdata.foo))

    def test_encode_tone(self):
        self.driver._encode_tone(self.testdata.foo, 'Tone', 88.5, 'N')
        self.assertEqual(885, int(self.testdata.foo))

    def test_encode_tone_none(self):
        self.driver._encode_tone(self.testdata.foo, '', 67.0, 'N')
        self.assertEqual(16665, int(self.testdata.foo))