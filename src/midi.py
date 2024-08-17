import logging
import asyncio
import time
import math
import simpleobsws
import rtmidi

import utils

MIDI_SIGNATURE = 'X-Touch-Ext'
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

obs_inputs = {
    0: {"name": "CANCEL", "id": "0"},
    1: {"name": "RESET", "id": "1"}
}
 
class Device:
    def __init__(self):
        self.input = rtmidi.MidiIn()
        self.output = rtmidi.MidiOut()

        self.lock = asyncio.Lock()
        self.strips = []

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
            for i, port in enumerate(self.input.get_ports()):
                if MIDI_SIGNATURE in port:
                    self.input.open_port(i)
                    if not self.input.is_port_open():
                        logging.error('Failed to open port {} at idx: {}'.format(self.input.get_port_name(i), i))
                        return False
                    ret += 1
                    logging.info('Opened IN port at idx {}: {}'.format(i, self.input.get_port_name(i)))
                    break
            for i, port in enumerate(self.output.get_ports()):
                if MIDI_SIGNATURE in port:
                    self.output.open_port(i)
                    if not self.output.is_port_open():
                        logging.error('Failed to open port {} at idx: {}'.format(self.output.get_port_name(i), i))
                        return False
                    ret += 1
                    logging.info('Opened OUT port at idx {}: {}'.format(i, self.output.get_port_name(i)))
                    break
            return ret == 2
        return await asyncio.to_thread(do_open, self)

    async def create_strip(self):
        async with self.lock:
            strip = await asyncio.to_thread(Strip, self, len(self.strips))
            await asyncio.to_thread(strip.reset)
            self.strips.append(strip)

    async def clear_strips(self):
        async with self.lock:
            self.strips = []

async def filter_audio_inputs(my_reqs):
    global obs_inputs

    ret = await obs.ws.call_batch(my_reqs, halt_on_failure=False)

    for idx, result in enumerate(ret, 2):
        if not result.ok():
            obs_inputs.pop(idx)

    obs_inputs = {new_key: value for new_key, (old_key, value) in enumerate(obs_inputs.items())}

def my_map(x, in_min, in_max, out_min, out_max):
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

class Strip:
    led_modes = {
        0: (1, 11),
        1: (17, 27),
        2: (65, 75),
        3: (81, 91),
    }

    def __init__(self, midi: Device, num: int):
        self.midi = midi
        self.num = num
        self.enc_mode = 3
        self.enc_value = -81
        self.rec = 0
        self.solo = 0
        self.mute = 0
        self.select = 0
        self.color_cnt = 7
        self.color_idx = 7
        self.option = 0
        self.source_name = ""
        self.source_uuid = ""
        self.source_cnt = 0
        self.source_idx = 0
        self.fader_current = 0
        self.fader_busy = 0
        self.fader_delta = 0

    def reset(self):
        # reset internal variables
        self.enc_mode = 3
        self.enc_value = -81
        self.rec = 0
        self.solo = 0
        self.mute = 0
        self.select = 0
        self.color_cnt = 7
        self.color_idx = 7
        self.option = 0
        self.source_name = ""
        self.source_uuid = ""
        self.source_cnt = 0
        self.source_idx = 0
        self.fader_current = 0
        self.fader_busy = 0
        self.fader_delta = 0

        # reset LCD color
        self.change_lcd_color(self.color_idx)

        # reset LCD text
        self.write_text(0, "")
        self.write_text(1, "")

        # power off encoder leds
        self.midi.output.send_message([176, self.num + 48, 0])

        # power off buttons
        self.midi.output.send_message([144, self.num, 0])
        self.midi.output.send_message([144, self.num + 8, 0])
        self.midi.output.send_message([144, self.num + 16, 0])

        # reset fader
        self.midi.output.send_message([self.num + 224, 1, 0])

    def restore(self):
        # restore internal variables (counters)
        self.source_cnt = self.source_idx
        self.color_cnt = self.color_idx
        self.select = 0

        # restore text
        self.write_text(0, self.source_name)
        self.write_text(1, "")

        # restore LCD color
        self.change_lcd_color(self.color_idx)

        # restore buttons leds
        self.midi.output.send_message([144, self.num, self.rec * 127])
        self.midi.output.send_message([144, self.num + 8, self.solo * 127])
        self.midi.output.send_message([144, self.num + 16, self.mute * 127])
        self.midi.output.send_message([144, self.num + 24, self.select])

        # restore encoder leds
        final_value = self.enc_value + self.led_modes[self.enc_mode][0]
        self.midi.output.send_message([176, self.num + 48, final_value])

        # restore fader
        self.midi.output.send_message([self.num + 224, 1, self.fader_current])

    async def process_button(self, msg):
        button = msg[0]
        value = msg[1]

        if button == self.num:  # REC button TRACK
            if value == 127:
                if self.select == 0:
                    if self.source_name != "":
                        self.rec = 1 - self.rec
                        self.midi.output.send_message([144, self.num, self.rec * 127])
                        await obs.call("SetInputAudioTracks", {"inputUuid": self.source_uuid, "inputAudioTracks": {"2": bool(self.rec)}})

        elif button == self.num + 8:  # SOLO button
            if value == 127:
                if self.select == 0:
                    if self.source_name != "":
                        self.solo = 1 - self.solo
                        if self.solo == 1:
                            monitor_type = "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT"
                        else:
                            monitor_type = "OBS_MONITORING_TYPE_NONE"

                        self.midi.output.send_message([144, self.num + 8, self.solo * 127])
                        await obs.call("SetInputAudioMonitorType", {"inputUuid": self.source_uuid, "monitorType": monitor_type})

        elif button == self.num + 16:  # MUTE button
            if value == 127:
                if self.select == 0:
                    if self.source_name != "":
                        self.mute = 1 - self.mute
                        self.midi.output.send_message([144, self.num + 16, self.mute * 127])
                        await obs.call("SetInputMute", {"inputUuid": self.source_uuid, "inputMuted": bool(self.mute)})

        elif button == self.num + 24:  # SELECT button
            if value == 127:
                # todo: listen to OBS EVENTS and cancel selection if sources changed while selecting
                # restore all the other strips
                for strip in self.midi.strips:
                    if strip.num != self.num:
                        strip.restore()

                # change select status
                self.select = 1 - self.select
                self.midi.output.send_message([144, self.num + 24, self.select])

                if self.select == 1:
                    # power off encoder leds
                    self.midi.output.send_message([176, self.num + 48, 0])

                    # power off buttons leds
                    self.midi.output.send_message([144, self.num, 0])
                    self.midi.output.send_message([144, self.num + 8, 0])
                    self.midi.output.send_message([144, self.num + 16, 0])

                    # get sources from obs
                    req_list = []
                    res = await obs.call("GetInputList")
                    for idx, inpt in enumerate(res["inputs"], 2):
                        obs_inputs[idx] = {"name": inpt["inputName"], "id": inpt["inputUuid"]}
                        req_list.append(simpleobsws.Request('GetInputAudioMonitorType', {"inputUuid": inpt["inputUuid"]}, ))
                    await filter_audio_inputs(req_list)

                    # update LCD text
                    if self.option == 0:
                        self.write_text(0, "SOURCE")
                        self.write_text(1, obs_inputs[self.source_idx]["name"])
                    else:
                        self.write_text(0, "COLOR")
                        self.write_text(1, MIDI_SCREEN_COLORS[self.color_idx])

                elif self.select == 0:
                    if self.option == 0:

                        # get current selection
                        source_selected_name = obs_inputs[self.source_cnt]["name"]
                        source_selected_idx = obs_inputs[self.source_cnt]["id"]

                        if source_selected_name == "CANCEL":
                            self.restore()

                        elif source_selected_name == "RESET":
                            self.reset()

                        else:
                            if self.source_uuid != source_selected_idx:
                                for strip in self.midi.strips:
                                    if strip.source_uuid == source_selected_idx:
                                        self.color_cnt = strip.color_idx
                                        self.color_idx = strip.color_idx
                                        self.enc_mode = strip.enc_mode

                            if self.source_uuid != source_selected_idx:
                                # get OBS states to update button states
                                current_solo = await obs.call("GetInputAudioMonitorType", {"inputUuid": source_selected_idx})
                                current_solo = current_solo["monitorType"]
                                current_mute = await obs.call("GetInputMute", {"inputUuid": source_selected_idx})
                                current_mute = current_mute["inputMuted"]
                                current_balance = await obs.call("GetInputAudioBalance", {"inputUuid": source_selected_idx})
                                current_balance = current_balance["inputAudioBalance"] * 10
                                current_slider = await obs.call("GetInputVolume", {"inputUuid": source_selected_idx})
                                current_slider = utils.x32_db_to_fader_val(current_slider["inputVolumeDb"])
                                current_track = await obs.call("GetInputAudioTracks", {"inputUuid": source_selected_idx})
                                current_track = int(current_track["inputAudioTracks"]["2"])

                                # update internal variables
                                if current_solo == "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT":
                                    self.solo = 1
                                else:
                                    self.solo = 0

                                self.enc_value = current_balance
                                self.rec = current_track
                                self.mute = int(current_mute)
                                self.fader_current = current_slider
                                self.source_name = source_selected_name
                                self.source_uuid = source_selected_idx
                                self.source_idx = self.source_cnt

                            # update LCD Text
                            self.write_text(0, self.source_name)
                            self.write_text(1, "")

                            # update LCD color
                            self.change_lcd_color(self.color_idx)
                            self.color_cnt = self.color_idx

                            # update buttons leds
                            self.midi.output.send_message([144, self.num, self.rec * 127])
                            self.midi.output.send_message([144, self.num + 8, self.solo * 127])
                            self.midi.output.send_message([144, self.num + 16, self.mute * 127])

                            # update encoder leds
                            final_value = self.enc_value + self.led_modes[self.enc_mode][0]
                            self.midi.output.send_message([176, self.num + 48, final_value])

                            # update fader
                            self.midi.output.send_message([self.num + 224, 1, self.fader_current])

                            # reset strips that previously have the current selection
                            for strip in self.midi.strips:
                                if strip.source_uuid == source_selected_idx and strip.num != self.num:
                                    strip.reset()

                    elif self.option == 1:
                        self.color_idx = self.color_cnt
                        self.restore()

        elif button == self.num + 32:  # ENCODER button
            if value == 127:
                if self.select == 0:
                    if self.source_idx != 0:
                        # update encoder mode
                        self.enc_mode = self.enc_mode + 1
                        if self.enc_mode > (len(self.led_modes) - 1):
                            self.enc_mode = 0

                        # update encoder lights
                        final_value = self.enc_value + self.led_modes[self.enc_mode][0]
                        self.midi.output.send_message([176, self.num + 48, final_value])

                elif self.select == 1:
                    self.option = 0 ** self.option

                    if self.option == 0:
                        self.write_text(0, "SOURCE")
                        self.write_text(1, obs_inputs[self.source_cnt]["name"])
                    else:
                        self.write_text(0, "COLOR")
                        self.write_text(1, MIDI_SCREEN_COLORS[self.color_cnt])

        else:
            logging.info('Unhandled event on strip: {}'.format(self.num))

    async def process_encoder(self, msg):
        if self.select == 0:
            if self.source_idx != 0:

                if msg[1] < 50:
                    self.enc_value = self.enc_value + 1
                    if self.enc_value > 10:
                        self.enc_value = 10

                elif msg[1] > 50:
                    self.enc_value = self.enc_value - 1
                    if self.enc_value < 0:
                        self.enc_value = 0

                await obs.call("SetInputAudioBalance", {"inputUuid": self.source_uuid, "inputAudioBalance": self.enc_value / 10})

        if self.select == 1:
            if msg[1] < 50:
                if self.option == 0:
                    self.source_cnt = self.source_cnt + 1
                    if self.source_cnt > (len(obs_inputs) - 1):
                        self.source_cnt = len(obs_inputs) - 1
                    self.write_text(0, "SOURCE")
                    self.write_text(1, obs_inputs[self.source_cnt]["name"])
                elif self.option == 1:
                    self.color_cnt = self.color_cnt + 1
                    if self.color_cnt > 8:
                        self.color_cnt = 1
                    self.write_text(0, "COLOR")
                    self.write_text(1, MIDI_SCREEN_COLORS[self.color_cnt])
                    self.change_lcd_color(self.color_cnt)

            elif msg[1] > 50:
                if self.option == 0:
                    self.source_cnt = self.source_cnt - 1
                    if self.source_cnt < 0:
                        self.source_cnt = 0
                    self.write_text(0, "SOURCE")
                    self.write_text(1, obs_inputs[self.source_cnt]["name"])
                else:
                    self.color_cnt = self.color_cnt - 1
                    if self.color_cnt < 1:
                        self.color_cnt = 8
                    self.write_text(0, "COLOR")
                    self.write_text(1, MIDI_SCREEN_COLORS[self.color_cnt])
                    self.change_lcd_color(self.color_cnt)

    async def process_fader(self, msg):
        if self.source_name != "":
            if self.select == 0:
                self.fader_current = msg[1]
                self.fader_delta = time.time_ns()
                self.fader_busy = 1

                db = utils.x32_fader_val_to_db(msg[1])
                req = simpleobsws.Request("SetInputVolume", {"inputUuid": self.source_uuid, "inputVolumeDb": db})
                await obs.ws.emit(req)

    def pos_fader(self):
        self.midi.output.send_message([self.num + 224, 1, self.fader_current])
        self.fader_busy = 0

    def write_text(self, line, my_str):
        if not (0 <= line <= 1):
            print("wrong LCD line")
            return

        my_str = my_str[:7]
        if not my_str:
            my_str = '       ' # In some cases, writing an empty string to the LCD will do nothing

        # Clear LCD text
        self.midi.output.send_message([
            0xF0,  # MIDI System Exclusive Start
            0x00, 0x00, 0x66,  # Header of Mackie Control Protocol
            0x15,  # Device vendor ID
            0x12,  # Command: Update LCD
            0x00 + (7 * self.num) + (56 * line),  # Offset (starting position in LCD) 0x00 to 0x37 for the upper line and 0x38 to 0x6F for the lower line
            0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,  # Chars to display in UTF-16
            0xF7  # MIDI System Exclusive End
        ])

        # write LCD text
        payload = [0xF0, 0x00, 0x00, 0x66, 0x15, 0x12, 0x00 + (7 * self.num) + (56 * line)]
        text = [ord(char) for char in my_str]
        payload.extend(text)
        payload.append(0xF7)
        self.midi.output.send_message(payload)

    def change_lcd_color(self, clr):
        payload = [0xF0, 0x00, 0x00, 0x66, 0x15, 0x72]

        for strip in self.midi.strips:
            payload.append(strip.color_idx)

        payload.append(0xF7)

        payload[self.num + 6] = clr

        self.midi.output.send_message(payload)

    def update_volumeter(self, obs_event_data):
        if self.select == 0:
            average_mul = [channel[1] for channel in obs_event_data]
            average_mul = sum(average_mul) / len(average_mul)

            if average_mul > 0:
                current_peak_db = 20 * math.log10(average_mul)

                if current_peak_db < -60:
                    current_peak_db = -60
                elif current_peak_db > -4:
                    current_peak_db = 0

                midi_value = my_map(current_peak_db, -60, 0, 0, 14)
                self.midi.output.send_message([208, (self.num * 16 + midi_value), 0])

    def update_fader(self, obs_event_data):
        if self.fader_busy:
            return

        db = obs_event_data["inputVolumeDb"]
        self.fader_current = utils.x32_db_to_fader_val(db)

        logging.info('Set fader to {}'.format(self.fader_current))

        self.midi.output.send_message([self.num + 224, 1, self.fader_current])

    def update_mute(self, obs_event_data):
        if self.select == 0:
            self.mute = int(obs_event_data["inputMuted"])
            self.midi.output.send_message([144, self.num + 16, self.mute * 127])

    def update_track(self, obs_event_data):
        if self.select == 0:
            self.rec = int(obs_event_data["inputAudioTracks"]["2"])
            self.midi.output.send_message([144, self.num, self.rec * 127])

    def update_balance(self, obs_event_data):
        if self.select == 0:
            val = int(round(obs_event_data["inputAudioBalance"], 1) * 10)
            self.enc_value = val
            final_value = self.enc_value + self.led_modes[self.enc_mode][0]
            self.midi.output.send_message([176, self.num + 48, final_value])

    def update_monitor(self, obs_event_data):
        if self.select == 0:
            if obs_event_data["monitorType"] == "OBS_MONITORING_TYPE_NONE":
                self.solo = 0
            else:
                self.solo = 1
            self.midi.output.send_message([144, self.num + 8, self.solo * 127])
