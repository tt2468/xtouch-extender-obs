import logging
import time
import asyncio

import obs as obs_lib
import midi as midi_lib
import utils

logging.basicConfig(level = logging.INFO)

OBS_WEBSOCKET_URL = 'ws://127.0.0.1:4455'
OBS_WEBSOCKET_PASSWORD = 'testing'

FADER_TIMEOUT = 0.3
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
    midi = midi_lib.Device()
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

    obs.ws.register_event_callback(obs_volmeter_callback, "InputVolumeMeters")
    obs.ws.register_event_callback(obs_balance_callback, "InputAudioBalanceChanged")
    obs.ws.register_event_callback(obs_track_callback, "InputAudioTracksChanged")
    obs.ws.register_event_callback(obs_monitor_callback, "InputAudioMonitorTypeChanged")
    obs.ws.register_event_callback(obs_mute_callback, "InputMuteStateChanged")
    obs.ws.register_event_callback(obs_volume_callback, "InputVolumeChanged")

    await midi.create_strips(MIDI_STRIP_COUNT)
    midi.input.set_callback(on_midi_message, asyncio.get_running_loop())

    while True:
        for strip in midi.strips:
            if strip.state != strip.State.Active:
                continue
            if strip.stateData.fader_busy() == -1:
                await asyncio.to_thread(strip.stateData._render_fader)
        await asyncio.sleep(0.05)

    await obs.shutdown()
    await midi.clear_strips()

# todo implement RTP-MIDI (ethernet) protocol
asyncio.run(main())
