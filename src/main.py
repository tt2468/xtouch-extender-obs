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

async def obs_volumeter_callback(event_data):
    for source in event_data["inputs"]:
        for strip in midi.strips:
            if source["inputUuid"] == strip.source_uuid:
                # Ignore empty lists
                if source["inputLevelsMul"]:
                    strip.update_volumeter(source["inputLevelsMul"])
                break


# todo identify event to merge all callbacks
async def obs_slider_callback(event_data):
    for strip in midi.strips:
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_fader(event_data)
            break

async def obs_mute_callback(event_data):
    for strip in midi.strips:
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_mute(event_data)
            break

async def obs_track_callback(event_data):
    for strip in midi.strips:
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_track(event_data)
            break

async def obs_balance_callback(event_data):
    for strip in midi.strips:
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_balance(event_data)
            break

async def obs_monitor_callback(event_data):
    for strip in midi.strips:
        if event_data["inputUuid"] == strip.source_uuid:
            strip.update_monitor(event_data)
            break

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

    midi_lib.obs = obs
    obs.ws.register_event_callback(obs_balance_callback, "InputAudioBalanceChanged")
    obs.ws.register_event_callback(obs_track_callback, "InputAudioTracksChanged")
    obs.ws.register_event_callback(obs_monitor_callback, "InputAudioMonitorTypeChanged")
    obs.ws.register_event_callback(obs_mute_callback, "InputMuteStateChanged")
    obs.ws.register_event_callback(obs_volumeter_callback, "InputVolumeMeters")
    obs.ws.register_event_callback(obs_slider_callback, "InputVolumeChanged")

    for i in range(MIDI_STRIP_COUNT):
        await midi.create_strip()

    while True:
        current = time.time_ns()
        for strip in midi.strips:
            if strip.fader_busy and current - strip.fader_delta > FADER_TIMEOUT * 1000000000:
                strip.pos_fader()
                await asyncio.sleep(0)

        midi_msg = midi.input.get_message()
        if not midi_msg:
            await asyncio.sleep(0)
            continue
        b1 = midi_msg[0][0]
        b2 = midi_msg[0][1]
        b3 = midi_msg[0][2]

        if b1 == 144:
            strip = midi.strips[b2 % 8]
            await strip.process_button([b2, b3])
        elif b1 == 176:
            strip = midi.strips[b2 % 8]
            await strip.process_encoder([b2, b3])
        else:
            strip = midi.strips[b1 - 224]
            await strip.process_fader([b1, b3])

        await asyncio.sleep(0)

    await obs.shutdown()
    await midi.clear_strips()

# todo implement RTP-MIDI (ethernet) protocol
asyncio.run(main())
