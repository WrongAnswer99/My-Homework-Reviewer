"""Shared official SDK transport for configured compatible API providers."""
from __future__ import annotations

import importlib.util
import ipaddress
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from api_config import endpoint_url

# The live probe can install dependencies locally without changing the user's
# Python environment. Normal installations use requirements.txt instead.
if importlib.util.find_spec('openai') is None:
    packages = Path(__file__).resolve().parent/'data/sdk_packages'
    if packages.is_dir():
        sys.path.insert(0, str(packages))

from openai import APIConnectionError, APIStatusError, APITimeoutError, DefaultHttpxClient, OpenAI
from httpx import HTTPError, TimeoutException


def sdk_base_url(base_url):
    endpoint = urlsplit(endpoint_url(base_url))
    return urlunsplit((endpoint.scheme, endpoint.netloc,
                      endpoint.path.removesuffix('/chat/completions'), '', ''))


def create_client(profile, *, base_url=None, timeout=None):
    target = base_url or sdk_base_url(profile['base_url'])
    host = urlsplit(target).hostname or ''
    local = host.lower() == 'localhost'
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    return OpenAI(base_url=target,
                  api_key=profile['api_key'], max_retries=0,
                  timeout=profile['timeout'] if timeout is None else timeout,
                  http_client=DefaultHttpxClient(follow_redirects=False, trust_env=not local))
