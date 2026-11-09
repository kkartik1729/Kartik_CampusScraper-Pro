"""Adapters for observed official directory endpoints, never inferred contacts."""
from urllib.parse import urlsplit, parse_qs, urlencode

MIT_HOST = 'mitwpu.edu.in'
MIT_PATH = '/faculty/showFacultyDataAjax'


def directory_links(soup, url):
    p = urlsplit(url)
    if p.hostname not in {MIT_HOST, 'www.' + MIT_HOST} or p.path != '/faculty-members':
        return []
    # The public page supplies the school IDs. Do not guess undocumented IDs.
    return [f'{p.scheme}://{p.netloc}{MIT_PATH}?' + urlencode({'school_id': o['value'], 'page': 0})
            for o in soup.select('#fac_school option[value]') if o['value'].isdigit()]


def directory_form(url):
    p = urlsplit(url)
    q = parse_qs(p.query)
    if p.hostname not in {MIT_HOST, 'www.' + MIT_HOST} or p.path != MIT_PATH:
        return None
    school = q.get('school_id', [''])[0]
    page = q.get('page', ['0'])[0]
    if not school.isdigit() or not page.isdigit():
        return None
    return dict(str='', school_id=school, search_key='', department='', page=page, limit='100')


def directory_next(url, html):
    form = directory_form(url)
    if not form or '###' not in html:
        return None
    total = html.rsplit('###', 1)[-1].strip()
    page = int(form['page'])
    if total.isdigit() and (page + 1) * 100 < int(total):
        p = urlsplit(url)
        return f'{p.scheme}://{p.netloc}{p.path}?' + urlencode({'school_id': form['school_id'], 'page': page + 1})
    return None
