#!/usr/bin/env python3
"""
Parser for cty.dat (CT9 "Country File") as used by ham radio contest
logging software (CT, N1MM, etc.) to resolve DXCC entities from callsigns.

Format reference: https://www.country-files.com/cty-dat-format/

Each entity record looks like:

    Country Name:CQ:ITU:Cont:Lat:Long:GMTOff:PrimaryPfx:
        =ALIAS1,ALIAS2(14)[27],ALIAS3{EU}<40.5/-74.0>~-5~;

- The 8 header fields are colon-delimited.
- A "*" preceding the primary prefix marks a WAEDC (DARC) entity.
- Alias prefixes (including the primary one) follow, comma-separated,
  possibly spanning multiple lines, terminated by ";".
- A leading "=" on an alias means it must match a *full callsign* exactly,
  not just a prefix.
- Trailing modifiers on an alias override fields for that alias only:
    (#)      override CQ zone
    [#]      override ITU zone
    <lat/lon> override latitude/longitude
    {aa}     override continent
    ~#~      override local GMT offset
"""

from __future__ import annotations

import functools
import hashlib
import logging
import pickle
import re
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

__all__ = ['load_cty']

__version__ = '0.0.4'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

SOURCE_URL = "https://www.country-files.com/cty/cty_wt_mod.dat"
DATA_PATH = "/tmp/cty_wt_mod.dat"

MAX_CACHE = 86400 * 7      # One week

_ALIAS_OVERRIDE_RE = re.compile(
  r"\((?P<cq>-?\d+)\)"
  r"|\[(?P<itu>-?\d+)\]"
  r"|<(?P<lat>-?\d+(?:\.\d+)?)/(?P<lon>-?\d+(?:\.\d+)?)>"
  r"|\{(?P<cont>[A-Za-z]{2})\}"
  r"|~(?P<gmt>-?\d+(?:\.\d+)?)~"
)


@dataclass(slots=True)
class Entity:  # pylint: disable=too-many-instance-attributes
  """A DXCC entity (country) entry from cty.dat."""
  country: str
  cq_zone: int
  itu_zone: int
  continent: str
  latitude: float
  longitude: float
  gmt_offset: float
  primary_prefix: str
  waedc: bool = False


@dataclass(slots=True)
class PrefixEntry:
  """One alias/prefix mapping to an (possibly overridden) Entity."""
  prefix: str
  exact_match: bool
  entity: Entity


def file_cache(cache_dir=".cache"):
  def decorator(func):
    # Create cache directory if it doesn't exist
    Path(cache_dir).mkdir(parents=True, exist_ok=True)

    def _gen_key(func_name, args):
      key_str = '.'.join([str(a) for a in args]).encode('utf-8')
      _hash = hashlib.blake2b(digest_size=8)
      _hash.update(key_str)
      return f"{func_name}-{_hash.hexdigest()}"

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
      # Create a unique cache key from arguments
      cache_key = _gen_key(func.__name__, args)
      cache_file = Path(cache_dir) / f"{cache_key}.pkl"
      logging.info(cache_file)

      # Try to load from cache
      if cache_file.exists() and cache_file.stat().st_mtime + MAX_CACHE > time.time():
        try:
          with open(cache_file, 'rb') as f:
            result = pickle.load(f)
            logging.warning("Cache hit: loaded from %s", cache_file)
            return result
        except Exception as e:  # pylint: disable=broad-exception-caught
          logging.error("Cache read error: %s", e)

      # Call the function and cache the result
      result = func(*args, **kwargs)

      try:
        with open(cache_file, 'wb') as f:
          pickle.dump(result, f)
        logging.info("Cache miss: saved to %s", cache_file)
      except Exception as e:  # pylint: disable=broad-exception-caught
        logging.error("Cache write error: %s", e)

      return result

    return wrapper
  return decorator


def _parse_header(fields: list[str]) -> Entity:
  country, cq_zone, itu_zone, continent, lat, lon, gmt_offset, primary = fields
  waedc = primary.strip().startswith("*")
  primary = primary.strip().lstrip("*")
  return Entity(
    country=country.strip(),
    cq_zone=int(cq_zone.strip()),
    itu_zone=int(itu_zone.strip()),
    continent=continent.strip(),
    latitude=float(lat.strip()),
    longitude=float(lon.strip()),
    gmt_offset=float(gmt_offset.strip()) * -1,
    primary_prefix=primary,
    waedc=waedc,
  )


def _parse_alias(raw: str, base_entity: Entity) -> Optional[PrefixEntry]:
  raw = raw.strip()
  if not raw or raw.startswith('#'):
    return None

  exact_match = raw.startswith("=")
  if exact_match:
    raw = raw[1:]

  m = re.match(r"^([^\(\[\{<~]+)(.*)$", raw)
  if not m:
    return None
  prefix, overrides_str = m.group(1).strip(), m.group(2)

  overrides: dict[str, Any] = {}
  for om in _ALIAS_OVERRIDE_RE.finditer(overrides_str):
    if om.group("cq"):
      overrides["cq_zone"] = int(om.group("cq"))
    elif om.group("itu"):
      overrides["itu_zone"] = int(om.group("itu"))
    elif om.group("lat"):
      overrides["latitude"] = float(om.group("lat"))
      overrides["longitude"] = float(om.group("lon"))
    elif om.group("cont"):
      overrides["continent"] = om.group("cont")
    elif om.group("gmt"):
      overrides["gmt_offset"] = float(om.group("gmt")) * -1

  entity = replace(base_entity, **overrides) if overrides else base_entity
  return PrefixEntry(prefix=prefix, exact_match=exact_match, entity=entity)


def parse_cty_dat(filepath: Path) -> dict[str, PrefixEntry]:
  """
  Parse a cty.dat file and return a dict mapping each alias prefix
  (or full callsign, for "=" entries) to a PrefixEntry.

  If a prefix appears more than once, the first occurrence wins,
  matching the "parse top to bottom, ignore duplicates" rule from
  the format spec.
  """
  with filepath.open("r", encoding="utf-8", errors="replace") as fd:
    lines = (ln.strip() for ln in fd if not ln.startswith('#'))
    content = '\n'.join(lines)

  prefixes: dict[str, PrefixEntry] = {}

  for record in content.split(";"):
    record = record.strip()
    if not record:
      continue

    parts = record.split(":", 8)
    if len(parts) < 8:
      continue  # malformed/trailing junk

    base_entity = _parse_header(parts[:8])
    aliases_str = parts[8] if len(parts) > 8 else ""
    aliases_str = aliases_str.replace("\n", " ").replace("\r", " ")

    for raw_alias in aliases_str.split(","):
      entry = _parse_alias(raw_alias, base_entity)
      if entry is None:
        continue
      key = entry.prefix
      if key not in prefixes:
        prefixes[key] = entry

  return prefixes


@dataclass(slots=True)
class _TrieNode:
  entry: Optional[PrefixEntry] = None
  children: dict = field(default_factory=dict)


class CtyTable:
  # pylint: disable=too-few-public-methods
  """
  Callsign -> DXCC entity lookup, built once from parse_cty_dat() output.

  Longest-prefix matching is done via a character trie, so lookup() is
  O(len(callsign)) regardless of how many prefixes are loaded (cty.dat
  typically has 20,000+ of them), instead of O(n) over every prefix.
  "=exact" full-callsign entries are kept in a separate dict for O(1)
  lookup and take priority, per the format spec.
  """

  def __init__(self, prefixes: dict[str, PrefixEntry]):
    self._exact: dict[str, PrefixEntry] = {}
    self._root = _TrieNode()

    for key, entry in prefixes.items():
      if entry.exact_match:
        self._exact[key] = entry
        continue
      node = self._root
      for ch in key:
        node = node.children.setdefault(ch, _TrieNode())
      node.entry = entry

  def lookup(self, callsign: str) -> Optional[PrefixEntry]:
    callsign = callsign.strip().upper()
    exact = self._exact.get(callsign)
    if exact is not None:
      return exact

    node = self._root
    best: Optional[PrefixEntry] = None
    for ch in callsign:
      if (next_node := node.children.get(ch)) is None:
        break
      node = next_node
      if node.entry is not None:
        best = node.entry
    return best


def url_retrieve(url: str, filepath: Path):
  etag_file = filepath.with_suffix('.etag')

  try:
    with open(etag_file, 'rb') as f:
      etag = f.read().strip()
  except FileNotFoundError:
    etag = None

  headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:155.0) Gecko/20100101 CtyParser"}
  if etag:
    headers["If-None-Match"] = str(etag)

  request = Request(url, headers=headers)
  try:
    with urlopen(request) as response:
      with open(filepath, "wb") as f:
        shutil.copyfileobj(response, f)

    new_etag = response.headers.get("ETag")
    if new_etag:
      with open(etag_file, "wb") as f:
        f.write(new_etag)

    logging.info("Downloaded: %s, etag: %s", url, new_etag)

  except HTTPError as e:
    if e.code == 304:
      logging.info("File has not changed")
    else:
      raise


@file_cache('/tmp')
def load_cty(filepath: Path | str = DATA_PATH) -> CtyTable:
  if isinstance(filepath, str):
    filepath = Path(filepath)

  url_retrieve(SOURCE_URL, filepath)
  prefixes = parse_cty_dat(filepath)
  logging.info("Loaded %s prefixes", len(prefixes))
  table = CtyTable(prefixes)
  return table


def lookup_callsign(callsign: str, prefixes: dict[str, PrefixEntry]) -> Optional[PrefixEntry]:
  """
  Resolve a callsign to its DXCC entity using longest-prefix matching,
  with "=exact" full-callsign entries taking priority over prefix matches.

  Convenience wrapper for one-off lookups. It builds a fresh CtyTable
  (and thus a fresh trie) on every call, so it's still O(n) overall for
  that call and is NOT suitable for looking up many callsigns — build a
  CtyTable once with CtyTable(prefixes) and call .lookup() instead.
  """
  return CtyTable(prefixes).lookup(callsign)


def main():
  import sys  # pylint: disable=import-outside-toplevel

  if len(sys.argv) < 1:
    print("Usage: python dxcty_parser.py [CALLSIGN ...]")
    raise SystemExit(1)

  table = load_cty()

  for call in sys.argv[1:]:
    result = table.lookup(call)
    if result:
      e = result.entity
      print(f"{call}: {e.country} ({e.primary_prefix}) CQ={e.cq_zone} "
            f"ITU={e.itu_zone} Cont={e.continent} GMT={e.gmt_offset} "
            f"Latitude={e.latitude}, Longitude={e.longitude}")
    else:
      print(f"{call}: no match")


if __name__ == "__main__":
  main()
