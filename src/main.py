import logging
import time
import asyncio
import argparse

import obs as obs_lib
import midi as midi_lib
import utils

logging.basicConfig(level = logging.DEBUG)

OBS_WEBSOCKET_URL = 'ws://localhost:4455'
OBS_WEBSOCKET_PASSWORD = ''

FADER_TIMEOUT = 0.3
MIDI_DEVICE_SIGNATURE = 'X-Touch-Ext'
MIDI_DEVICE_INDEX = 0
MIDI_STRIP_COUNT = 8

obs = None
midi = None

async def obs_volmeter_callback(event_data):
    for input in event_data["inputs"]:
        uuid = input['inputUuid']
        strip = midi.stripInputUuids.get(uuid)
        if not strip:
            continue
        strip.on_input_volmeter(input['inputLevelsMul'])

async def obs_balance_callback(event_data):
    uuid = event_data['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    strip.on_input_balance_change(event_data)

async def obs_track_callback(event_data):
    uuid = event_data['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    strip.on_input_track_change(event_data)

async def obs_monitor_callback(event_data):
    uuid = event_data['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    strip.on_input_monitor_change(event_data)

async def obs_mute_callback(event_data):
    uuid = event_data['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    strip.on_input_mute_change(event_data)

async def obs_volume_callback(event_data):
    uuid = event_data['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    strip.on_input_volume_change(event_data)

def on_midi_message(msg, loop):
    if not msg:
        return

    b1 = msg[0][0]
    b2 = msg[0][1]
    b3 = msg[0][2]

    try:
        if b1 == 144:
            strip = midi.strips[b2 % 8]
            async def process(val):
                try:
                    await strip.process_button(val)
                except:
                    logging.exception('Exception:\n')
            asyncio.run_coroutine_threadsafe(process([b2, b3]), loop)
        elif b1 == 176:
            strip = midi.strips[b2 % 8]
            async def process(val):
                try:
                    await strip.process_encoder(val)
                except:
                    logging.exception('Exception:\n')
            asyncio.run_coroutine_threadsafe(process([b2, b3]), loop)
        else:
            strip = midi.strips[b1 - 224]
            async def process(val):
                try:
                    await strip.process_fader(val)
                except:
                    logging.exception('Exception:\n')
            asyncio.run_coroutine_threadsafe(process([b1, b3]), loop)
    except:
        logging.exception('Exception:\n')

async def main():
    global midi
    midi = midi_lib.Device(MIDI_DEVICE_SIGNATURE, MIDI_DEVICE_INDEX)
    await midi.print_ports()
    if not await midi.open_ports():
        logging.critical('Failed to open MIDI ports!')
        return

    global obs
    obs = obs_lib.ObsStudio(OBS_WEBSOCKET_URL, OBS_WEBSOCKET_PASSWORD)
    try:
        if not await obs.startup():
            logging.critical('Failed to connect or identify with OBS.')
            return
    except:
        logging.exception('Failed to connect or identify with OBS:\n')
        return
    midi.set_obs(obs)
    logging.info('Connected and identified with obs-websocket at URL: {}'.format(OBS_WEBSOCKET_URL))

    obs.ws.register_event_callback(obs_volmeter_callback, "InputVolumeMeters")
    obs.ws.register_event_callback(obs_balance_callback, "InputAudioBalanceChanged")
    obs.ws.register_event_callback(obs_track_callback, "InputAudioTracksChanged")
    obs.ws.register_event_callback(obs_monitor_callback, "InputAudioMonitorTypeChanged")
    obs.ws.register_event_callback(obs_mute_callback, "InputMuteStateChanged")
    obs.ws.register_event_callback(obs_volume_callback, "InputVolumeChanged")

    await midi.create_strips(MIDI_STRIP_COUNT)
    midi.input.set_callback(on_midi_message, asyncio.get_running_loop())

    logging.info('Finished starting up.')

    try:
        while True:
            for strip in midi.strips:
                if strip.state != strip.State.Active:
                    continue
                if strip.stateData.fader_busy() == -1:
                    await asyncio.to_thread(strip.stateData._render_fader)
            await asyncio.sleep(0.05)
    except asyncio.exceptions.CancelledError:
        logging.info('Shutting down...')

    await obs.shutdown()
    await midi.clear_strips()

def process_args():
    global OBS_WEBSOCKET_URL
    global OBS_WEBSOCKET_PASSWORD
    global MIDI_DEVICE_SIGNATURE
    global MIDI_DEVICE_INDEX
    global MIDI_STRIP_COUNT

    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--websocket_url', type = str, default = OBS_WEBSOCKET_URL, help = 'obs-websocket URL. Default: {}'.format(OBS_WEBSOCKET_URL))
    parser.add_argument('-p', '--websocket_password', type = str, default = '', help = 'obs-websocket Password. Default is none.')
    parser.add_argument('-s', '--midi_signature', type = str, default = MIDI_DEVICE_SIGNATURE, help = 'MIDI device signature - a string to look for in the device name. Default: {}'.format(MIDI_DEVICE_SIGNATURE))
    parser.add_argument('-d', '--midi_device', type = int, default = 0, help = 'MIDI device index to select out of the devices matching the signature. Default: 0')
    parser.add_argument('-S', '--midi_strip_count', type = int, default = MIDI_STRIP_COUNT, help = 'Number of strips that the device has. Default: {}'.format(MIDI_STRIP_COUNT))

    args = parser.parse_args()
    OBS_WEBSOCKET_URL = args.websocket_url
    OBS_WEBSOCKET_PASSWORD = args.websocket_password
    MIDI_DEVICE_SIGNATURE = args.midi_signature
    MIDI_DEVICE_INDEX = args.midi_device
    MIDI_STRIP_COUNT = args.midi_strip_count

# todo implement RTP-MIDI (ethernet) protocol
if __name__ == "__main__":
    process_args()
    asyncio.run(main())
