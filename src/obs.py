import logging
import bisect
import asyncio
import simpleobsws
from dataclasses import dataclass, field

@dataclass
class Input:
    uuid: str = ''
    name: str = ''
    kind: str = ''

    supportsAudio: bool = False
    audioVolumeDb: float = -100.0
    audioMuted: bool = False
    audioBalance: float = 0.5
    audioMonitorType: str = ''
    audioTracks: dict[str, bool] = field(default_factory = dict)

    @staticmethod
    def from_obsws_data(data):
        return Input(data['inputUuid'], data['inputName'], data['inputKind'])

    async def hydrate(self, ws) -> bool:
        requests = [
            simpleobsws.Request('GetInputVolume', {'inputUuid': self.uuid}),
            simpleobsws.Request('GetInputMute', {'inputUuid': self.uuid}),
            simpleobsws.Request('GetInputAudioBalance', {'inputUuid': self.uuid}),
            simpleobsws.Request('GetInputAudioMonitorType', {'inputUuid': self.uuid}),
            simpleobsws.Request('GetInputAudioTracks', {'inputUuid': self.uuid})
        ]
        responses = await ws.call_batch(requests, halt_on_failure = True)
        if not responses[0].ok():
            self.supportsAudio = False
        else:
            self.supportsAudio = True
            self.audioVolumeDb = responses[0].responseData['inputVolumeDb']
            self.audioMuted = responses[1].responseData['inputMuted']
            self.audioBalance = responses[2].responseData['inputAudioBalance']
            self.audioMonitorType = responses[3].responseData['monitorType']
            self.audioTracks = responses[4].responseData['inputAudioTracks']
        return True

class ObsStudio:
    def __init__(self, websocketUrl, websocketPassword = None):
        parameters = simpleobsws.IdentificationParameters()
        parameters.eventSubscriptions = (1 << 3) | (1 << 16)
        self.ws = simpleobsws.WebSocketClient(url = websocketUrl, password = websocketPassword, identification_parameters=parameters)
        self.ws.register_event_callback(self._event_on_input_created, "InputCreated")
        self.ws.register_event_callback(self._event_on_input_removed, "InputRemoved")

        self.inputsLock = asyncio.Lock()
        self.inputs = {}
        self.inputNames = []

    async def startup(self) -> bool:
        if not await self.ws.connect():
            return False
        if not await self.ws.wait_until_identified():
            return False
        await self._refresh_input_list()
        return True

    async def shutdown(self):
        await self.ws.disconnect()
        self.ws = None

    async def call(self, requestType, requestData = None) -> dict:
        req = simpleobsws.Request(requestType, requestData)
        resp = await self.ws.call(req)
        if not resp.ok():
            raise Exception('`{}` request returned invalid status: {} | Comment: {}'.format(ret.requestStatus.code, ret.requestStatus.comment))
        return resp.responseData

    async def _refresh_input_list(self):
        resp = await self.call('GetInputList')
        async with self.inputsLock:
            self.inputs = {}
            for inputData in resp['inputs']:
                input = Input.from_obsws_data(inputData)
                await input.hydrate(self.ws)
                self.inputs[input.uuid] = input
                self.inputNames.insert(bisect.bisect_left([x[0] for x in self.inputNames], input.name), [input.name, input])

    async def _event_on_input_created(self, eventData):
        input = Input.from_obsws_data(eventData)
        async with self.inputsLock:
            self.inputs[input.uuid] = input
            self.inputNames.insert(bisect.bisect_left([x[0] for x in self.inputNames], input.name), [input.name, input])

    async def _event_on_input_removed(self, eventData):
        inputUuid = eventData['inputUuid']
        async with self.inputsLock:
            if inputUuid not in self.inputs:
                return
            del self.inputs[inputUuid]
            self.inputNames = [x for x in self.inputNames if x.uuid != inputUuid]

    async def _event_on_input_renamed(self, eventData):
        pass # TODO
