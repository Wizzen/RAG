from __future__ import annotations

"""当前页面的临时代理状态；保留真实消息和浏览器，不写会话历史到数据库。"""

import threading
import time

TTL=1800
_entries={}
_lock=threading.Lock()
_timer=None
_workers={}
_abandoned=set()


def begin(job_id):
    with _lock:_workers[str(job_id)]=threading.Event()


def end(job_id):
    with _lock:
        key=str(job_id);event=_workers.pop(key,None);_abandoned.discard(key)
    if event:event.set()


def _close(state):
    browser=state.get('browser')
    if browser:
        try:browser.close()
        except Exception:pass


def _expire():
    global _timer
    expired=[]
    with _lock:
        for key,entry in list(_entries.items()):
            if entry['expires']<time.monotonic():expired.append(_entries.pop(key)['state'])
        _timer=None
        if _entries:_schedule()
    for state in expired:_close(state)


def _schedule():
    global _timer
    if _timer is None:
        _timer=threading.Timer(60,_expire);_timer.daemon=True;_timer.start()


def put(job_id,user_id,state):
    evicted=[]
    with _lock:
        key=str(job_id)
        if key in _abandoned:
            threading.Thread(target=_close,args=(state,),daemon=True).start();return
        if key in _entries:evicted.append(_entries.pop(key)['state'])
        while len(_entries)>=32:evicted.append(_entries.pop(next(iter(_entries)))['state'])
        _entries[key]={'user':user_id,'expires':time.monotonic()+TTL,'state':state};_schedule()
    for value in evicted:
        if value.get('browser') is not state.get('browser'):
            threading.Thread(target=_close,args=(value,),daemon=True).start()


def take(job_id,user_id):
    # 预览状态可先于工作线程最后的清理被前端看到；等它交接状态再接续。
    with _lock:event=_workers.get(str(job_id))
    if event:event.wait(10)
    with _lock:
        entry=_entries.get(str(job_id))
        if not entry or entry['user']!=user_id:return None
        _entries.pop(str(job_id))
    if entry['expires']<time.monotonic():_close(entry['state']);return None
    return entry['state']


def touch(job_id,user_id):
    with _lock:
        entry=_entries.get(str(job_id))
        if entry and entry['user']==user_id:entry['expires']=time.monotonic()+TTL


def discard(ids,user_id):
    closed=[]
    with _lock:
        for value in ids:
            key=str(value);entry=_entries.get(key)
            if key in _workers:_abandoned.add(key)
            if entry and entry['user']==user_id:closed.append(_entries.pop(key)['state'])
    for state in closed:threading.Thread(target=_close,args=(state,),daemon=True).start()


def complete_tool_groups(messages):
    """被成功预览短路的并行工具明确标为未执行，保持下一轮工具消息配对。"""
    from langchain_core.messages import ToolMessage
    result=[];index=0
    while index<len(messages):
        message=messages[index];result.append(message);index+=1
        calls=getattr(message,'tool_calls',[]) or []
        if not calls:continue
        supplied=set()
        while index<len(messages) and isinstance(messages[index],ToolMessage):
            supplied.add(messages[index].tool_call_id);result.append(messages[index]);index+=1
        for call in calls:
            if call['id'] not in supplied:result.append(ToolMessage(content='本轮已结束，此工具未执行。',tool_call_id=call['id']))
    return result
