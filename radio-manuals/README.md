# Radio manuals

PDF radio manuals are **not included in this repository** — they are large and
their redistribution is usually restricted by the manufacturer's license.

To use the manuals feature, drop your own PDFs into this folder on the target
machine (the Raspberry Pi, your laptop, or the USB bundle):

```
radio-manuals/
├── YAESU/
│   └── FT-991A.pdf
├── ICOM/
│   └── IC-7300.pdf
└── ...
```

The dashboard's file browser will list whatever is here automatically — no
configuration needed. Organize them into per-manufacturer subfolders if you
like; the layout is up to you.

> Everything in this folder except this `README.md` is git-ignored, so your
> manuals will never be committed by accident.
