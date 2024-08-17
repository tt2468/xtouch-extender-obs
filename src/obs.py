import logging
import asyncio
import simpleobsws

class ObsStudio:
    def __init__(self, websocketUrl, websocketPassword = None):
        parameters = simpleobsws.IdentificationParameters()
        parameters.eventSubscriptions = (1 << 3) | (1 << 16)
        self.ws = simpleobsws.WebSocketClient(url = websocketUrl, password = websocketPassword, identification_parameters=parameters)

    async def startup(self):
        if not await self.ws.connect():
            return False
        if not await self.ws.wait_until_identified():
            return False
        return True

    async def shutdown(self):
        await self.ws.disconnect()
        self.ws = None

    async def call(self, requestType, requestData = None):
        req = simpleobsws.Request(requestType, requestData)
        resp = await self.ws.call(req)
        if not resp.ok:
            raise Exception('`{}` request returned invalid status: {} | Comment: {}'.format(ret.requestStatus.code, ret.requestStatus.comment))
        return resp.responseData
