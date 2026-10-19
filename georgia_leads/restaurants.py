"""Read Yell's public restaurant batches, as published by its own directory UI."""
import re
from urllib.parse import parse_qs, urlsplit, urlencode
from georgia_contacts.network import FetchError

BASE = 'https://www.yell.ge/restaurants.php?lan=eng'


def fetch_page(client, url, cursor):
    if 'restaurant_batches' not in cursor:
        _, html = client.fetch(BASE)
        match = re.search(rb'var\s+markers_mt\s*=\s*(\d+)\s*;', html)
        if not match:
            raise FetchError('unreachable', 'restaurant_version_missing')
        version = match[1].decode()
        _, payload = client.fetch('https://www.yell.ge/load_fad_.php', form={'n':'load_fad','i':'1','mt':version,'l':'eng'})
        text = payload.decode().strip()
        if not re.fullmatch(r'\d+(?:\.\d+(?:,\d+)*)+', text):
            raise FetchError('unreachable', 'restaurant_index_invalid')
        total, *batches = text.split('.')
        if len(batches)>500 or sum(len(b.split(',')) for b in batches)!=int(total):
            raise FetchError('unreachable', 'restaurant_index_count_mismatch')
        cursor.update(restaurant_version=version, restaurant_batches=batches, published_records=int(total))
    page = int(parse_qs(urlsplit(url).query).get('SR_pg',['1'])[0])
    batches = cursor['restaurant_batches']
    if not 1<=page<=len(batches):
        raise FetchError('unreachable', 'restaurant_page_out_of_range')
    _, body = client.fetch('https://www.yell.ge/SR_.php', form={'n':'fad','i':str(page),'mt':cursor['restaurant_version'],'l':'eng','x':batches[page-1]})
    if body.strip()==b'reload':
        cursor.pop('restaurant_batches',None)
        raise FetchError('unreachable', 'restaurant_catalog_changed')
    pages = ['https://www.yell.ge/restaurants.php?'+urlencode({'lan':'eng','SR_pg':str(n)}) for n in range(1,len(batches)+1)]
    return BASE, body, pages
