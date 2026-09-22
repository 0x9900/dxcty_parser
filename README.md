# dxcty_parser

Parse [cty.dat](https://www.country-files.com/cty-dat-format/) (the AD1C
"country file") and resolve amateur radio callsigns to DXCC entities —
country, CQ zone, ITU zone, continent, lat/long, and GMT offset.

## Usage

```python
from dxcty_parser import load_cty

table = load_cty()  # build once, reuse for many lookups

result = table.lookup("W1AW")
print(result.entity.country, result.entity.cq_zone)
```

## CLI

```
python dxcty_parser.py W1AW KM6ETX
```

## Requirements

Python 3.10+, standard library only.
