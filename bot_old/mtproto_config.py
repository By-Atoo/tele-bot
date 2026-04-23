# config.py для mtprotoproxy
PROXY = {
    'host': 'VIP.alotaxi.info',
    'port': 4515,
    'secret': '7umk8jsddowEqNfzkSDKW25iaXNjb3R0aS55ZWt0YW5ldC5jb20'  # из ссылки, без изменений
}

# Локальный SOCKS5-прокси будет доступен на 127.0.0.1:1080
SOCKS5 = ('127.0.0.1', 1080)

# Опционально: если нужно обходить только Telegram, укажите домены
ALLOWED_DOMAINS = ['api.telegram.org']