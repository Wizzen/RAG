"""下载并校验固定版本的 G6 浏览器包；不执行 npm 安装脚本。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

VERSION = '5.1.1'
INTEGRITY = 'sha512-50bXxMUf4mChyOv4ePVeWZLwotih9VunKfp0a++Wofv/wCyY8fb9+CV2wouIBCOZnd5ydBRA4NNaX9yLJzqa2w=='
URL = f'https://registry.npmjs.org/@antv/g6/-/g6-{VERSION}.tgz'


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / 'static' / 'js' / 'vendor'
    data = urllib.request.urlopen(URL, timeout=45).read()
    actual = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual != INTEGRITY:
        raise ValueError('G6 包校验失败，未写入文件。')
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member, filename in [('package/dist/g6.min.js', 'g6-5.1.1.min.js'), ('package/LICENSE', 'g6-LICENSE.txt')]:
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f'包缺少文件：{member}')
            (destination / filename).write_bytes(source.read())
    (destination / 'g6-provenance.json').write_text(json.dumps({
        'package': '@antv/g6', 'version': VERSION, 'url': URL, 'integrity': INTEGRITY,
        'license': 'MIT', 'repository': 'https://github.com/antvis/G6',
    }, indent=2) + '\n', encoding='utf-8')
    print(f'G6 {VERSION} 已校验并保存到 {destination}')


if __name__ == '__main__':
    main()
