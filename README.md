# dxcty_parser

Parse [cty.dat](https://www.country-files.com/cty-dat-format/) (the AD1C
"country file") and resolve amateur radio callsigns to DXCC entities —
country, CQ zone, ITU zone, continent, lat/long, and GMT offset.

## Usage

```python
>>> from dxcty_parser import load_cty
>>> table = load_cty()  # build once, reuse for many lookups
>>> result = table.lookup("W1AW")
>>> print(f'Result: {result.entity.country}, {result.entity.itu_zone}')
Result: United States, 8
```

## CLI

This is intended to be used as a library. The CLI is just for testing purpose.

```
% dxcty_parser W1AW KM6ETX
W1AW: United States (K) CQ=5 ITU=8 Cont=NA GMT=-5.0 Latitude=42.38, Longitude=71.65
KM6ETX: United States (K) CQ=3 ITU=6 Cont=NA GMT=-8.0 Latitude=35.48, Longitude=119.35
```

## Requirements

Python 3.10+, standard library only.
