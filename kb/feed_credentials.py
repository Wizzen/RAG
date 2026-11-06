from __future__ import annotations

"""凭据录入、自动加密、主机绑定和运行时注入；公开接口只返回引用与状态。"""

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from .feed_protocol import validate_url
from .models import FeedCredential

LABELS = {'bearer':'Bearer Token','api_key':'API Key','basic':'Basic 用户名与密码','login':'网页登录账号与密码','headers':'自定义请求头'}


def api_origin(url: str) -> str:
    """统一 scheme、主机与实际端口，凭据不跟随跨主机请求。"""
    validate_url(url,allow_fragment=True)
    parts = urlsplit(url)
    return json.dumps([parts.scheme.lower(),parts.hostname.lower(),parts.port or (443 if parts.scheme=='https' else 80)])


def _cipher(*, create: bool = False) -> Fernet:
    """首次自动创建独立随机密钥，后续进程复用；丢失密钥时不静默替换。"""
    path = Path(settings.DATA_DIR)/'secrets'/'feed-credentials.key'
    if create and not path.exists():
        if FeedCredential.objects.exists(): raise ValueError('凭据加密密钥不可用，请恢复部署备份中的密钥文件。')
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'wb') as stream: stream.write(Fernet.generate_key())
        except FileExistsError: pass
    for attempt in range(3):
        try: return Fernet(path.read_bytes())
        except ValueError:
            if attempt<2: time.sleep(0.02)
        except OSError: break
    raise ValueError('凭据加密密钥不可用，请恢复部署备份中的密钥文件。')


# 调用方（尤其是本地模型）经常省略 type：只有一种含义明确时才推断，否则报错，
# 绝不静默按「不认证」处理——那会导凭据从未保存而后续每次请求都 401。
INFER = {'token': 'bearer', 'value': 'api_key', 'header_name': 'api_key',
         'username': 'basic', 'password': 'basic', 'headers': 'headers'}
AUTH_HELP = ('请明确给出 type：none / bearer(token) / api_key(header_name,value) / '
             'basic(username,password) / login(username,password) / headers([{name,value}])。')


def normalize_auth(auth: dict) -> dict:
    """补全缺失的 `type`；歧义结构直接报错，不猜。空字典＝显式不认证（页面默认）。"""
    kind = auth.get('type', None)
    if kind is not None and kind != '':
        return auth
    if not auth:
        return {'type': 'none'}
    guesses = {INFER[key] for key in auth if key in INFER}
    if len(guesses) != 1:
        raise ValueError('认证配置缺少 type，且无法安全推断，不会按「不认证」处理。' + AUTH_HELP)
    return {**auth, 'type': guesses.pop()}


def parse_auth(auth: dict) -> tuple[str,dict,list[str]]:
    """将易读表单转换为请求头，并拒绝控制字符、重复头与连接头覆盖。"""
    from .feed_http import validate_headers
    if not isinstance(auth,dict): raise ValueError('认证配置格式无效。')
    auth = normalize_auth(auth)
    kind = auth.get('type','none')
    def value(key: str, label: str) -> str:
        result = auth.get(key,'')
        if not isinstance(result,str) or not result or len(result)>8000 or any(ord(ch)<32 or ord(ch)==127 for ch in result):
            raise ValueError('请填写有效的'+label+'，不能包含换行。')
        return result
    extra = []
    if kind=='none': return kind,{},[]
    if kind=='bearer':
        token = value('token','Token').strip()
        token = re.sub(r'^Bearer\s+','',token,flags=re.I)
        if not token or any(ch.isspace() for ch in token): raise ValueError('Token 格式无效，请粘贴完整令牌。')
        headers = {'Authorization':'Bearer '+token};extra=[token]
    elif kind=='api_key': headers={value('header_name','请求头名称').strip():value('value','API Key')}
    elif kind=='basic':
        username=value('username','用户名');password=value('password','密码')
        if ':' in username: raise ValueError('Basic 用户名不能包含冒号。')
        headers={'Authorization':'Basic '+base64.b64encode((username+':'+password).encode()).decode()};extra=[password]
    elif kind=='login':
        extra=[value('username','网页登录账号'),value('password','网页登录密码')];headers={}
    elif kind=='headers':
        rows=auth.get('headers')
        if not isinstance(rows,list) or not 1<=len(rows)<=10: raise ValueError('请填写1至10项请求头名称和值。')
        headers={}
        for row in rows:
            if not isinstance(row,dict): raise ValueError('请求头格式无效。')
            name=row.get('name');content=row.get('value')
            if not isinstance(name,str) or not isinstance(content,str) or not content or len(content)>8000 or any(ord(ch)<32 or ord(ch)==127 for ch in content): raise ValueError('请求头名称和值不能为空或包含换行。')
            if name.lower() in {key.lower() for key in headers}: raise ValueError('请求头名称不能重复。')
            headers[name]=content
    else: raise ValueError('请选择支持的认证方式。')
    validate_headers({key:'FOS_FEED_CHECK' for key in headers})
    return kind,headers,list(dict.fromkeys([*headers.values(),*extra]))


def bindings(credential: FeedCredential) -> dict:
    """返回脚本和模型可使用的非密钥引用；无需手动配置这些变量。"""
    return {name:f'FOS_FEED_CREDENTIAL_{credential.pk.hex.upper()}_{index}' for index,name in enumerate(credential.header_names)}


def store(user, url: str, auth: dict) -> FeedCredential | None:
    """将页面中的认证值加密保存；永不写入 prompt、spec 或日志。"""
    kind,headers,secrets = parse_auth(auth)
    if kind=='none': return None
    origin = api_origin(url)
    payload={'owner':str(user.pk),'origin':origin,'headers':headers,'secrets':secrets,
             'login':{'username':auth['username'],'password':auth['password']} if kind=='login' else {}}
    encrypted=_cipher(create=True).encrypt(json.dumps(payload,ensure_ascii=False).encode()).decode()
    return FeedCredential.objects.create(owner=user,kind=kind,api_origin=origin,header_names=list(headers),encrypted_value=encrypted)


def unpack(credential: FeedCredential) -> dict:
    """校验密文完整性及归属后在内存中解密，异常不包含密钥或密文。"""
    if not credential.owner_id or not credential.owner.is_active: raise ValueError('凭据所属账号已停用。')
    try: data=json.loads(_cipher().decrypt(credential.encrypted_value.encode()))
    except (InvalidToken,ValueError,TypeError) as exc: raise ValueError('无法解密 API 凭据，请在页面重新保存。') from exc
    if data.get('owner')!=str(credential.owner_id) or data.get('origin')!=credential.api_origin: raise ValueError('API 凭据归属校验未通过。')
    return data


def secret_values(resource) -> tuple[str,...]:
    credential=getattr(resource,'credential',None)
    if credential: return tuple(unpack(credential)['secrets'])
    return tuple(os.environ.get(name,'') for name in resource.header_env.values())


def request_headers(resource, url: str) -> dict:
    """工具与定时任务共用凭据解析；只注入到用户确认的同一来源。"""
    from .feed_http import environment_headers
    credential=getattr(resource,'credential',None)
    if not credential: return environment_headers(resource.header_env)
    if api_origin(url)!=credential.api_origin: raise ValueError('API 主机发生变化，请在页面重新填写该主机的凭据。')
    if resource.header_env!=bindings(credential): raise ValueError('认证引用已变化，请重新保存凭据。')
    return unpack(credential)['headers']


def summary(resource) -> dict:
    credential=getattr(resource,'credential',None)
    if credential:
        return {'configured':True,'type':credential.kind,'label':LABELS.get(credential.kind,'API 认证'),
                'headers':credential.header_names,'managed':True,'login_fields':['username','password'] if credential.kind=='login' else []}
    return {'configured':bool(resource.header_env),'type':'legacy' if resource.header_env else 'none',
            'label':'已有环境变量认证' if resource.header_env else '无需认证','headers':list(resource.header_env),'managed':False}


def discard_if_unused(credential_id) -> None:
    if credential_id:
        FeedCredential.objects.filter(pk=credential_id,feeds__isnull=True,drafts__isnull=True).delete()
