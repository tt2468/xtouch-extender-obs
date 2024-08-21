import logging
import time
import asyncio
import argparse

import obs as obs_lib
import midi as midi_lib
import utils

logging.basicConfig(level = logging.DEBUG)
logging.getLogger('simpleobsws').setLevel(logging.INFO)

CONFIG_FILE_NAME = 'xtouch-obs-config.json'

OBS_WEBSOCKET_URL = 'ws://localhost:4455'
OBS_WEBSOCKET_PASSWORD = ''

FADER_TIMEOUT = 0.3
MIDI_DEVICE_SIGNATURE = 'X-Touch-Ext'
MIDI_DEVICE_INDEX = 0
MIDI_STRIP_COUNT = 8

config = None
obs = None
midi = None

async def obs_volmeter_callback(eventData):
    for input in eventData["inputs"]:
        uuid = input['inputUuid']
        strip = midi.stripInputUuids.get(uuid)
        if not strip:
            continue
        strip.on_input_volmeter(input['inputLevelsMul'])

async def obs_balance_callback(eventData):
    uuid = eventData['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    await strip.on_input_balance_change(eventData)

async def obs_track_callback(eventData):
    uuid = eventData['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    await strip.on_input_track_change(eventData)

async def obs_monitor_callback(eventData):
    uuid = eventData['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    await strip.on_input_monitor_change(eventData)

async def obs_mute_callback(eventData):
    uuid = eventData['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    await strip.on_input_mute_change(eventData)

async def obs_volume_callback(eventData):
    uuid = eventData['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    await strip.on_input_volume_change(eventData)

async def obs_input_name_changed_callback(eventData):
    uuid = eventData['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    # Prior callbacks would have updated the input name in the object already
    await strip.on_input_name_change()

async def obs_input_remove_callback(eventData):
    uuid = eventData['inputUuid']
    strip = midi.stripInputUuids.get(uuid)
    if not strip:
        return
    await asyncio.to_thread(strip.reset)

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
        logging.exception('Exception when handling incoming MIDI message:\n')

async def main():
    global config
    config = utils.Config()
    if not config.load(CONFIG_FILE_NAME):
        logging.warning('Config file `{}` not loaded. Using default config.')

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
    obs.ws.register_event_callback(obs_input_name_changed_callback, "InputNameChanged")
    obs.ws.register_event_callback(obs_input_remove_callback, "InputRemoved")

    await midi.create_strips(MIDI_STRIP_COUNT)
    await midi.load_strips(config)
    if len(midi.strips) != len(config.strips):
        oldConfigStrips = len(config.strips)
        await midi.persist_strips(config)
        config.save(CONFIG_FILE_NAME)
        logging.info('Updated config file from {} to {} strips.'.format(oldConfigStrips, len(config.strips)))
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
    try:
        await midi.persist_strips(config)
        if config.save(CONFIG_FILE_NAME):
            logging.info('Config file `{}` saved.'.format(CONFIG_FILE_NAME))
        else:
            logging.info('Failed to save config file: {}'.format(CONFIG_FILE_NAME))

        await obs.shutdown()
        await midi.clear_strips()

        logging.info('Finished shutting down.')
    except:
        logging.exception('Exception:\n')

def process_args():
    global CONFIG_FILE_NAME
    global OBS_WEBSOCKET_URL
    global OBS_WEBSOCKET_PASSWORD
    global MIDI_DEVICE_SIGNATURE
    global MIDI_DEVICE_INDEX
    global MIDI_STRIP_COUNT

    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config_file', type = str, default = CONFIG_FILE_NAME, help = 'Config/state file name, used for persistence of configurations made via the X-Touch device. Default: {}'.format(CONFIG_FILE_NAME))
    parser.add_argument('-u', '--websocket_url', type = str, default = OBS_WEBSOCKET_URL, help = 'obs-websocket URL. Default: {}'.format(OBS_WEBSOCKET_URL))
    parser.add_argument('-p', '--websocket_password', type = str, default = '', help = 'obs-websocket Password. Default is none.')
    parser.add_argument('-s', '--midi_signature', type = str, default = MIDI_DEVICE_SIGNATURE, help = 'MIDI device signature - a string to look for in the device name. Default: {}'.format(MIDI_DEVICE_SIGNATURE))
    parser.add_argument('-d', '--midi_device', type = int, default = 0, help = 'MIDI device index to select out of the devices matching the signature. Default: 0')
    parser.add_argument('-S', '--midi_strip_count', type = int, default = MIDI_STRIP_COUNT, help = 'Number of strips that the device has. Default: {}'.format(MIDI_STRIP_COUNT))

    args = parser.parse_args()
    CONFIG_FILE_NAME = args.config_file
    OBS_WEBSOCKET_URL = args.websocket_url
    OBS_WEBSOCKET_PASSWORD = args.websocket_password
    MIDI_DEVICE_SIGNATURE = args.midi_signature
    MIDI_DEVICE_INDEX = args.midi_device
    MIDI_STRIP_COUNT = args.midi_strip_count

# todo implement RTP-MIDI (ethernet) protocol
if __name__ == "__main__":
    process_args()
    asyncio.run(main())
