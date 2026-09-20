MusicSocial V4 batch upgrade (features 1-8)

Replace Android MainActivity.kt with MainActivity_V4_ALL_FEATURES.kt.
Replace backend main.py with main_V4_COMPLETE.py.

Backend start:
python -m uvicorn main:app --host 0.0.0.0 --port 8000

The upgrade adds search, hashtags, creator profile messaging entry, notification read support, vertical-style autoplay feed foundation, music pages, DMs and creator credits/boost dashboard.

Real payment settlement and real-time LIVE video transport are not included; those require payment/streaming provider integration.
