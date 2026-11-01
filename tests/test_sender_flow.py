import asyncio
import base64
import json
import time

import pytest

from companion.sender import Sender
from test_workflows import store


@pytest.mark.asyncio
async def test_audio_sender_still_transmits_when_screen_sharing_is_disabled(store,monkeypatch):
    """The optional picture gate must never gate the independent microphone path."""
    store.put("sender_token","synthetic-pairing-key")
    store.put("sender_config",{"server":"http://127.0.0.1:8787","sources":[{"id":"pc_mic","name":"Mic","role":"streamer"}],"monitor":0})
    sender=Sender(store)
    sender.running=True
    frames=[]
    steps=0
    class Socket:
        async def send(self,raw):
            event=json.loads(raw);frames.append(event)
        async def recv(self):
            nonlocal steps
            steps+=1
            if steps==1:return json.dumps({"type":"connected","running":False})
            if steps==2:return json.dumps({"type":"configured"})
            if steps==3:
                sender.queue.put_nowait({"type":"audio","source":"pc_mic","pcm":base64.b64encode(b"\x01\x00"*2400).decode()})
                return json.dumps({"type":"heartbeat","running":True,"live":True,"paused":False,"images_enabled":False})
            for _ in range(20):
                if any(event.get("type")=="audio" for event in frames):break
                await asyncio.sleep(0)
            raise asyncio.CancelledError()
    class Connection:
        async def __aenter__(self):return Socket()
        async def __aexit__(self,*args):pass
    monkeypatch.setattr("companion.sender.websockets.connect",lambda *args,**kwargs:Connection())
    with pytest.raises(asyncio.CancelledError):await sender.connection()
    assert any(event.get("type")=="audio" for event in frames)
    assert not any(event.get("type")=="image" for event in frames)
    assert sender.sent_bytes==4800
    await sender.stop()
