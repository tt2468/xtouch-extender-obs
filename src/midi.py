import logging
import asyncio
import time
import copy
import math
import simpleobsws
import rtmidi
from enum import Enum

import obs
import utils

MIDI_SCREEN_COLORS = {
    1: "RED",
    2: "GREEN",
    3: "YELLOW",
    4: "BLUE",
    5: "MAGENTA",
    6: "CYAN",
    7: "WHITE",
    8: "BLACK"
}
MIDI_LED_MODES = [
    (1, 11),
    (17, 27),
    (65, 75),
    (81, 91)
]

def my_map(x, in_min, in_max, out_min, out_max):
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

class Device:
    def __init__(self, deviceSignature: str, deviceIndex: int = 0):
        self.obs = None
        self.deviceSignature = deviceSignature
        self.deviceIndex = deviceIndex

        self.input = rtmidi.MidiIn()
        self.output = rtmidi.MidiOut()

        self.lock = asyncio.Lock()
        self.strips = []
        self.stripInputUuids = {}

    async def print_ports(self):
        def do_print(midi):
            for i, port in enumerate(midi.get_ports()):
                logging.info('  - {} | {}'.format(i, midi.get_port_name(i)))
        logging.info('MIDI Ins:')
        await asyncio.to_thread(do_print, self.input)
        logging.info('MIDI Outs:')
        await asyncio.to_thread(do_print, self.output)

    async def open_ports(self):
        def do_open(self):
            ret = 0
            foundIndex = 0
            for i, port in enumerate(self.input.get_ports()):
                if self.deviceSignature in port:
                    if self.deviceIndex != foundIndex:
                        foundIndex += 1
                        continue
                    self.input.open_port(i)
                    if not self.input.is_port_open():
                        logging.error('Failed to open port {} at idx: {}'.format(self.input.get_port_name(i), i))
                        return False
                    ret += 1
                    logging.info('Opened IN port at idx {}: {}'.format(i, self.input.get_port_name(i)))
                    break
            foundIndex = 0
            for i, port in enumerate(self.output.get_ports()):
                if self.deviceSignature in port:
                    if self.deviceIndex != foundIndex:
                        foundIndex += 1
                        continue
                    self.output.open_port(i)
                    if not self.output.is_port_open():
                        logging.error('Failed to open port {} at idx: {}'.format(self.output.get_port_name(i), i))
                        return False
                    ret += 1
                    logging.info('Opened OUT port at idx {}: {}'.format(i, self.output.get_port_name(i)))
                    break
            return ret == 2
        return await asyncio.to_thread(do_open, self)

    def set_obs(self, obs: obs.ObsStudio):
        self.obs = obs

    async def create_strips(self, num: int):
        async with self.lock:
            self.strips = []
            for i in range(num):
                strip = await asyncio.to_thread(Strip, self, len(self.strips))
                self.strips.append(strip)

    async def load_strips(self, config: utils.Config):
        async with self.lock:
            for i, strip in enumerate(self.strips):
                if len(config.strips) <= i:
                    break
                await strip.load_config(config.strips[i])

    async def persist_strips(self, config: utils.Config):
        async with self.lock:
            config.strips = []
            for strip in self.strips:
                config.strips.append(strip.get_config())

    async def clear_strips(self):
        async with self.lock:
            for strip in self.strips:
                await asyncio.to_thread(strip.reset) # Sets the panel state back to default
            self.strips = []

    def _set_lcd_color(self, num: int, colorIdx: int):
        payload = [0xF0, 0x00, 0x00, 0x66, 0x15, 0x72]
        for strip in self.strips:
            payload.append(strip.stateData.lcdColorIdx if strip.state == Strip.State.Active else 7)
        payload.append(0xF7)
        payload[num + 6] = colorIdx

        self.output.send_message(payload)

    def _write_text(self, num: int, line: int, text):
        if not (0 <= line <= 1):
            print("wrong LCD line")
            return

        text = text[:7]
        if not text:
            text = '       ' # In some cases, writing an empty string to the LCD will do nothing

        # Clear LCD text
        self.output.send_message([
            0xF0,  # MIDI System Exclusive Start
            0x00, 0x00, 0x66,  # Header of Mackie Control Protocol
            0x15,  # Device vendor ID
            0x12,  # Command: Update LCD
            0x00 + (7 * num) + (56 * line),  # Offset (starting position in LCD) 0x00 to 0x37 for the upper line and 0x38 to 0x6F for the lower line
            0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,  # Chars to display in UTF-16
            0xF7  # MIDI System Exclusive End
        ])

        # write LCD text
        payload = [0xF0, 0x00, 0x00, 0x66, 0x15, 0x12, 0x00 + (7 * num) + (56 * line)]
        payload.extend([ord(char) for char in text])
        payload.append(0xF7)
        self.output.send_message(payload)

    def _set_led_encoder(self, num: int, val: int):
        self.output.send_message([176, num + 48, val])
    def _set_led_rec(self, num: int, on: bool):
        self.output.send_message([144, num, 127 if on else 0])
    def _set_led_solo(self, num: int, on: bool):
        self.output.send_message([144, num + 8, 127 if on else 0])
    def _set_led_mute(self, num: int, on: bool):
        self.output.send_message([144, num + 16, 127 if on else 0])
    def _set_led_select(self, num: int, on: bool):
        self.output.send_message([144, num + 24, 127 if on else 0])

    def _set_fader_pos(self, num: int, pos: int):
        self.output.send_message([num + 224, 1, pos])

    def _set_volmeter_db(self, num: int, db: float): # TODO: Use correct scale for this
        midi_value = my_map(db, -60, 0, 0, 14)
        self.output.send_message([208, (num * 16 + midi_value), 0])

class Strip:
    class State(Enum):
        Idle = 0
        Active = 1
        Config = 2

    class StateDataIdle:
        def __init__(self, midi: Device, num: int):
            self.midi = midi
            self.num = num

            self.lcdColorIdx = 7

        def render(self):
            if not self.midi:
                return
            logging.debug('Rendering State: Idle')
            self.midi._set_lcd_color(self.num, self.lcdColorIdx)
            self.midi._write_text(self.num, 0, '')
            self.midi._write_text(self.num, 1, '')
            self.midi._set_led_encoder(self.num, 0)
            self.midi._set_led_rec(self.num, False)
            self.midi._set_led_solo(self.num, False)
            self.midi._set_led_mute(self.num, False)
            self.midi._set_led_select(self.num, False)
            self.midi._set_fader_pos(self.num, 0)

    class StateDataActive:
        def __init__(self, midi: Device, num: int):
            self.midi = midi
            self.num = num

            self.input = None
            self.lcdColorIdx = 7

            self.faderTime = None

        def render(self):
            if not self.midi:
                return
            logging.debug('Rendering State: Active')
            self._render_leds()
            self._render_lcd()
            self._render_fader()

        def _render_leds(self):
            self.midi._set_led_encoder(self.num, round(self.input.audioBalance * 10) + MIDI_LED_MODES[1][0])
            self.midi._set_led_rec(self.num, self.input.audioTracks.get('2') or False)
            self.midi._set_led_solo(self.num, self.input.audioMonitorType == 'OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT')
            self.midi._set_led_mute(self.num, self.input.audioMuted)
            self.midi._set_led_select(self.num, False)

        def _render_lcd(self):
            self.midi._set_lcd_color(self.num, self.lcdColorIdx)
            self.midi._write_text(self.num, 0, self.input.name)
            self.midi._write_text(self.num, 1, '')

        def _render_fader(self):
            if self.fader_busy() == 1:
                return
            pos = utils.x32_db_to_fader_val(self.input.audioVolumeDb)
            self.midi._set_fader_pos(self.num, pos)

        def set_input(self, input: obs.Input):
            self.input = input

        def fader_busy(self) -> int:
            if not self.faderTime:
                return 0
            if time.time_ns() - self.faderTime > 800000000:
                self.faderTime = None
                return -1
            return 1

    class StateDataConfig:
        def __init__(self, midi: Device, num: int):
            self.midi = midi
            self.num = num

            self.menu = 0 # 0 == input, 1 == LCD color

            # Input Menu
            self.inputList = [
                ['CANCEL', True],
                ['RESET', None]
            ]
            for pair in midi.obs.inputNames:
                if not pair[1].supportsAudio:
                    continue
                self.inputList.append(copy.copy(pair))
            self.inputIdx = 0

            # LCD Color Menu
            self.lcdColorIdx = 7

        def render(self):
            if not self.midi:
                return
            logging.debug('Rendering State: Config')
            self._render_leds()
            self._render_lcd()

        def _render_leds(self):
            self.midi._set_led_encoder(self.num, 0)
            self.midi._set_led_rec(self.num, False)
            self.midi._set_led_solo(self.num, False)
            self.midi._set_led_mute(self.num, False)
            self.midi._set_led_select(self.num, True)

        def _render_lcd(self):
            if self.menu == 0:
                self.midi._write_text(self.num, 0, "SOURCE")
                self.midi._write_text(self.num, 1, self.inputList[self.inputIdx][0])
            elif self.menu == 1:
                self.midi._write_text(self.num, 0, "COLOR")
                self.midi._write_text(self.num, 1, MIDI_SCREEN_COLORS[self.lcdColorIdx])
            self.midi._set_lcd_color(self.num, self.lcdColorIdx)

        def iterate_menu(self):
            self.menu += 1
            if self.menu == 2:
                self.menu = 0
            self._render_lcd()

        def iterate_selection(self, offset):
            if self.menu == 0:
                self.inputIdx += offset
                if self.inputIdx < 0:
                    self.inputIdx = len(self.inputList) - 1
                elif self.inputIdx >= len(self.inputList):
                    self.inputIdx = 0
            elif self.menu == 1:
                self.lcdColorIdx += offset
                if self.lcdColorIdx < 1:
                    self.lcdColorIdx = len(MIDI_SCREEN_COLORS)
                elif self.lcdColorIdx > len(MIDI_SCREEN_COLORS):
                    self.lcdColorIdx = 1
            self._render_lcd()

    def __init__(self, midi: Device, num: int):
        self.midi = midi
        self.num = num
        self.state = self.State.Idle
        self.stateData = self.StateDataIdle(midi, num)
        self.oldState = None
        self.oldStateData = None
        self.enc_mode = 3
        self.enc_value = -81

        self.stateData.render()

    def get_config(self) -> utils.StripConfig:
        if self.state == self.State.Idle:
            return utils.StripConfig()
        if self.state == self.State.Active:
            return utils.StripConfig(obsInputUuid = self.stateData.input.uuid, lcdColorIdx = self.stateData.lcdColorIdx)
        if self.state == self.State.Config:
            if self.oldState == self.State.Active:
                return utils.StripConfig(obsInputUuid = self.oldStateData.input.uuid, lcdColorIdx = self.stateData.lcdColorIdx)
        return utils.StripConfig()

    async def load_config(self, config: utils.StripConfig):
        if self.state != self.State.Idle:
            await asyncio.to_thread(self.reset)
        if config.obsInputUuid and config.obsInputUuid in self.midi.obs.inputs:
            self.state = self.State.Active
            self.stateData = self.StateDataActive(self.midi, self.num)
            self.stateData.input = self.midi.obs.inputs[config.obsInputUuid]
            self.stateData.lcdColorIdx = config.lcdColorIdx
            self.midi.stripInputUuids[self.stateData.input.uuid] = self
            await asyncio.to_thread(self.stateData.render)
            logging.debug('Loaded input on strip {} - Name: {} | UUID: {}'.format(self.num, self.stateData.input.name, self.stateData.input.uuid))

    def reset(self):
        # reset internal variables
        if self.state == self.State.Active:
            if self.stateData.input.uuid in self.midi.stripInputUuids:
                del self.midi.stripInputUuids[self.stateData.input.uuid]
        self.state = self.State.Idle
        self.stateData = self.StateDataIdle(self.midi, self.num)
        self.oldState = None
        if self.oldStateData:
            self.oldStateData.midi = None
        self.oldStateData = None
        self.enc_mode = 3
        self.enc_value = -81

        self.stateData.render()

    def restore(self):
        if not self.oldState:
            return

        if self.state == self.State.Active:
            if self.stateData.input.uuid in self.midi.stripInputUuids:
                del self.midi.stripInputUuids[self.stateData.input.uuid]

        self.state = self.oldState
        self.oldState = None

        self.stateData = self.oldStateData
        self.oldStateData = None

        self.stateData.midi = self.midi
        self.stateData.render()

        if self.state == self.State.Active:
            self.midi.stripInputUuids[self.stateData.input.uuid] = self

    async def process_button(self, msg):
        button = msg[0]
        value = msg[1]

        if button == self.num + 32: # ENCODER button
            if not value:
                return
            if self.state == self.State.Active:
                # update encoder mode
                self.enc_mode = self.enc_mode + 1
                if self.enc_mode > (len(self.led_modes) - 1):
                    self.enc_mode = 0

                # update encoder lights
                final_value = self.enc_value + self.led_modes[self.enc_mode][0]
                self.midi.output.send_message([176, self.num + 48, final_value])

            elif self.state == self.State.Config:
                self.stateData.iterate_menu()

        elif button == self.num: # REC button TRACK
            if not value or self.state != self.State.Active: # Value will be 127 if pressed down, 0 if released
                return
            new = not (self.stateData.input.audioTracks.get('2') or False)
            await self.midi.obs.call('SetInputAudioTracks', {'inputUuid': self.stateData.input.uuid, 'inputAudioTracks': {'2': new}})

        elif button == self.num + 8: # SOLO button
            if not value or self.state != self.State.Active:
                return
            new = 'OBS_MONITORING_TYPE_NONE'
            if self.stateData.input.audioMonitorType == 'OBS_MONITORING_TYPE_NONE':
                new = 'OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT'
            else:
                new = 'OBS_MONITORING_TYPE_NONE'
            await self.midi.obs.call('SetInputAudioMonitorType', {'inputUuid': self.stateData.input.uuid, 'monitorType': new})

        elif button == self.num + 16: # MUTE button
            if not value or self.state != self.State.Active:
                return
            new = not self.stateData.input.audioMuted
            await self.midi.obs.call('SetInputMute', {'inputUuid': self.stateData.input.uuid, 'inputMuted': new})

        elif button == self.num + 24: # SELECT button
            if not value:
                return

            # Only allow one strip to be in config mode
            for strip in self.midi.strips:
                if strip.num != self.num:
                    strip.restore()

            if self.state == self.State.Config:
                newInput = self.stateData.inputList[self.stateData.inputIdx][1]
                lcdColorIdx = self.stateData.lcdColorIdx
                if newInput == True or (self.oldState == self.State.Active and self.oldStateData.input == newInput):
                    self.restore()
                    if self.state == self.State.Active:
                        self.stateData.lcdColorIdx = lcdColorIdx
                        self.stateData._render_lcd()
                elif not newInput:
                    self.reset()
                else:
                    for strip in self.midi.strips:
                        if strip.state == self.State.Active and strip.stateData.input.uuid == newInput.uuid:
                            strip.reset()
                    self.oldState = None
                    self.oldStateData = None
                    self.state = self.State.Active
                    self.stateData = self.StateDataActive(self.midi, self.num)
                    self.stateData.set_input(newInput)
                    self.stateData.lcdColorIdx = lcdColorIdx
                    self.stateData.render()
                    self.midi.stripInputUuids[newInput.uuid] = self
            else:
                if self.state == self.State.Active:
                    if self.stateData.input.uuid in self.midi.stripInputUuids:
                        del self.midi.stripInputUuids[self.stateData.input.uuid]
                self.oldState = self.state
                self.oldStateData = self.stateData
                self.oldStateData.midi = None
                self.state = self.State.Config
                self.stateData = self.StateDataConfig(self.midi, self.num)
                self.stateData.lcdColorIdx = self.oldStateData.lcdColorIdx
                self.stateData.render()

        else:
            logging.info('Unhandled event on strip: {}'.format(self.num))

    async def process_encoder(self, msg):
        if self.state == self.State.Active:
            logging.info('Encoder value: {}'.format(msg[1]))
            if msg[1] < 50:
                new = round(self.stateData.input.audioBalance + 0.1, 1)
                if new > 1.0:
                    new = 1.0
                await self.midi.obs.call('SetInputAudioBalance', {'inputUuid': self.stateData.input.uuid, 'inputAudioBalance': new})
            elif msg[1] > 50:
                new = round(self.stateData.input.audioBalance - 0.1, 1)
                if new < 0.0:
                    new = 0.0
                await self.midi.obs.call('SetInputAudioBalance', {'inputUuid': self.stateData.input.uuid, 'inputAudioBalance': new})
            logging.info('New: {}'.format(new))

        elif self.state == self.State.Config:
            if msg[1] < 50: # Turn clockwise
                self.stateData.iterate_selection(1)
            elif msg[1] > 50: # Turn counter-clockwise
                self.stateData.iterate_selection(-1)

    async def process_fader(self, msg):
        if self.state != self.State.Active:
            return

        self.stateData.faderTime = time.time_ns()

        db = utils.x32_fader_val_to_db(msg[1])
        req = simpleobsws.Request('SetInputVolume', {'inputUuid': self.stateData.input.uuid, 'inputVolumeDb': db})
        await self.midi.obs.ws.emit(req)

    def on_input_volmeter(self, data):
        if self.state != self.State.Active:
            return

        average_mul = [channel[1] for channel in data]
        average_mul = (sum(average_mul) / len(average_mul)) if average_mul else 0.0

        if average_mul > 0.0:
            current_peak_db = 20 * math.log10(average_mul)

            if current_peak_db < -60.0:
                current_peak_db = -60.0
            elif current_peak_db > -4.0:
                current_peak_db = 0.0

            self.midi._set_volmeter_db(self.num, current_peak_db)

    async def on_input_balance_change(self, data):
        if self.state != self.State.Active:
            return

        self.stateData.input.audioBalance = data['inputAudioBalance']
        await asyncio.to_thread(self.stateData._render_leds)

    async def on_input_track_change(self, data):
        if self.state != self.State.Active:
            return

        self.stateData.input.audioTracks = data['inputAudioTracks']
        await asyncio.to_thread(self.stateData._render_leds)

    async def on_input_monitor_change(self, data):
        if self.state != self.State.Active:
            return

        self.stateData.input.audioMonitorType = data['monitorType']
        await asyncio.to_thread(self.stateData._render_leds)

    async def on_input_mute_change(self, data):
        if self.state != self.State.Active:
            return

        self.stateData.input.audioMuted = data['inputMuted']
        await asyncio.to_thread(self.stateData._render_leds)

    async def on_input_volume_change(self, data):
        if self.state != self.State.Active:
            return

        self.stateData.input.audioVolumeDb = data['inputVolumeDb']
        await asyncio.to_thread(self.stateData._render_fader)

    async def on_input_name_change(self):
        if self.state != self.State.Active:
            return

        await asyncio.to_thread(self.stateData._render_lcd)
