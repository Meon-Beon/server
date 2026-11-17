"""보험다모아 어린이/태아보험: 장기·단기 전체 수집.

설치: python -m pip install pandas beautifulsoup4 playwright
      python -m playwright install chromium
VS Code에서 실행하면 입력 없이 같은 폴더에 wideform/longform CSV 저장.
longform의 상세구분: 보장(보장명/지급사유/보장금액), 특성(특성명/설명/수치).
수집 실패 시 정상 CSV를 덮어쓰지 않고 partial CSV와 진단 HTML 저장.
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

URL = 'https://www.e-insmarket.or.kr/guaranteeIns/guaranteeInsList.knia?menuId=C005'
ROOT = Path(__file__).resolve().parent
PERIODS = {'Y': '보험기간 10년 이상 (장기)', 'N': '보험기간 10년 미만 (단기)'}
WIDE_PATH = ROOT / '어린이_product_wideform.csv'
LONG_PATH = ROOT / '어린이_product_longform.csv'
ROWS = '.list_area tr[data-prdt-cd]'
REQUEST_INTERVAL_SECONDS = 10
LONG_COLUMNS = ['조회결과ID','상품코드','보험기간','회사명','상품명','상세구분','표시순서',
                '보장명','지급사유','보장금액','특성코드','특성명','표시내용','수치','단위','설명']


def clean(value):
    if value is None:
        return ''
    if hasattr(value, 'get_text'):
        value = value.get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', BeautifulSoup(str(value), 'html.parser').get_text(' ', strip=True)).strip()


def parse_icons(raw):
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        result = []
        for block in re.split(r'\},\s*\{', raw.strip().strip('[]')):
            block = block.strip().removeprefix('{').removesuffix('}')
            matches = list(re.finditer(r'(?:^|,\s*)(ICON_[A-Z_]+)=', block))
            icon = {}
            for i, match in enumerate(matches):
                end = matches[i+1].start() if i+1 < len(matches) else len(block)
                value = block[match.end():end].strip()
                icon[match.group(1)] = '' if value in ('null','undefined') else value
            if icon:
                result.append(icon)
        return result


def coverages(html):
    soup = BeautifulSoup(html, 'html.parser')
    result = []
    for li in soup.select('ul.item_list > li'):
        item = {'보장명': clean(li.select_one('.kind')),
                '지급사유': clean(li.select_one('.src')) or clean(li.get('data-pay-rsn')),
                '보장금액': clean(li.select_one('.cost'))}
        if item['보장명'] and item not in result:
            result.append(item)
    return result


def form_value(soup, name):
    node = soup.select_one(f'#searchForm [name="{name}"]')
    if node is None:
        raise ValueError(f'검색 폼 필드 없음: {name}')
    return node.get('value', '')


class RateLimitError(RuntimeError):
    pass


def check_blocked(html):
    text = clean(html).lower()
    if 'request flood control' in text or 'request rate of the session exceeded' in text:
        raise RateLimitError(
            '사이트 요청 빈도 제한(Request Flood Control)으로 차단되었습니다. '
            '자동 재시도하지 않습니다. 잠시 뒤 브라우저에서 보장내용 더보기가 '
            '정상적으로 열리는지 확인한 후 다시 실행하세요. 차단 해제 시간은 응답에 없습니다.')


def pause_requests(page):
    # 화면의 부가 리소스도 세션 요청 수에 포함될 수 있어 충분히 간격을 둔다.
    page.wait_for_timeout(REQUEST_INTERVAL_SECONDS * 1000)


def page_info(html):
    check_blocked(html)
    soup = BeautifulSoup(html, 'html.parser')
    count = soup.select_one('.total_count strong')
    if count is None:
        raise ValueError('상품 건수가 없습니다. 차단 또는 화면 변경 여부를 확인하세요.')
    total = int(re.sub(r'\D','',clean(count)))
    size = int(form_value(soup, 'pageSize'))
    if size <= 0:
        raise ValueError('잘못된 페이지 크기')
    return soup, total, size


def parse_products(html, period, number):
    soup, _, _ = page_info(html)
    records = []
    for row in soup.select(ROWS):
        logo = row.select_one('.comp_logo img')
        name = row.select_one('td.prod > p')
        premium = row.select_one('td.total_cost')
        if logo is None or name is None or premium is None:
            raise ValueError('상품 기본정보 구조가 변경되었습니다.')
        code = row['data-prdt-cd']
        if 'C005' not in code:
            raise ValueError(f'어린이보험이 아닌 상품 코드: {code}')
        tag = row.select_one('.tag_bizdiv')
        classes = tag.get('class',[]) if tag else []
        icons = row.select_one('[data-iconlist]')
        remark = row.select_one('[data-remark], [data-rmk]')
        last = row.find_all('td', recursive=False)[-1]
        price = clean(premium)
        record = {'조회결과ID': f'C005_{period}_{code}', '상품코드': code,
                  '보험기간': PERIODS[period], '회사명': clean(logo.get('alt')),
                  '상품명': clean(name), '보험구분': '생명보험' if 'life' in classes else
                  '손해보험' if any(x in classes for x in ('property','general')) else clean(tag),
                  '보험료': int(price.replace(',','')) if re.fullmatch(r'[\d,]+',price) else pd.NA,
                  '보험료_표시': price, '보험료단위': '원',
                  '가입연령': clean(premium.find_next_sibling('td')),
                  '기타사항': clean(remark.get('data-remark',remark.get('data-rmk',''))) if remark else '',
                  '가입형태': ' / '.join(clean(x) for x in last.select('span.item') if not x.select_one('button')),
                  '가입URL': ' / '.join(dict.fromkeys(x.get('data-url','') for x in last.select('[data-url]'))),
                  '가입기준': clean(soup.select_one('.join_rule .rule_src')),
                  '출처URL': URL, '수집페이지': number,
                  '수집시각': datetime.now().astimezone().isoformat(timespec='seconds'),
                  '보장수집완료': not bool(row.select_one('[onclick*="grntPopup"]')),
                  '보장수집상태': '목록 전체',
                  '_보장': coverages(str(row)),
                  '_특성': parse_icons(icons.get('data-iconlist','')) if icons else []}
        for icon in record['_특성']:
            title = clean(icon.get('ICON_TITLE'))
            record['특성_'+title] = clean(icon.get('ICON_SUB_TITLE'))
            record['특성_'+title+'_설명'] = clean(icon.get('ICON_DESCRIPTION'))
            if icon.get('ICON_PRICE_INDEX') not in ('',None):
                record['특성_'+title+'_수치'] = pd.to_numeric(icon['ICON_PRICE_INDEX'],errors='coerce')
        records.append(record)
    return records


def to_tables(records):
    wide, long = [], []
    for record in records:
        product = {k:v for k,v in record.items() if not k.startswith('_')}
        product['보장개수'] = len(record['_보장'])
        product['특성개수'] = len(record['_특성'])
        for key in ('보장명','지급사유','보장금액'):
            product[key] = '\n'.join(f'{i}. {x[key]}' for i,x in enumerate(record['_보장'],1))
        wide.append(product)
        common = {key: record[key] for key in LONG_COLUMNS[:5]}
        for i, item in enumerate(record['_보장'],1):
            long.append({**common,'상세구분':'보장','표시순서':i,**item})
        for i, icon in enumerate(record['_특성'],1):
            index = icon.get('ICON_PRICE_INDEX')
            value = pd.to_numeric(index,errors='coerce')
            long.append({**common,'상세구분':'특성',
                         '표시순서':pd.to_numeric(icon.get('ICON_ORDER',i),errors='coerce'),
                         '특성코드':clean(icon.get('ICON_CD')), '특성명':clean(icon.get('ICON_TITLE')),
                         '표시내용':clean(icon.get('ICON_SUB_TITLE')), '수치':value,
                         '단위':'%' if pd.notna(value) else '', '설명':clean(icon.get('ICON_DESCRIPTION'))})
    return pd.DataFrame(wide), pd.DataFrame(long,columns=LONG_COLUMNS)


def save_tables(records, wide_path, long_path):
    wide, long = to_tables(records)
    for frame, path in ((wide,Path(wide_path)),(long,Path(long_path))):
        path.parent.mkdir(parents=True,exist_ok=True)
        temp = path.with_suffix('.tmp')
        frame.to_csv(temp,index=False,encoding='utf-8-sig')
        temp.replace(path)
    return wide, long


def submit_page(page, period, number):
    pause_requests(page)
    with page.expect_navigation(wait_until='domcontentloaded',timeout=60000):
        page.evaluate('''([period,number]) => {
            const f=document.querySelector('#searchForm');
            if (!f) throw new Error('검색 폼 없음');
            const values={menuId:'C005',prdtSmlClsCd:'C005',enterType:'A',
                page:String(number),longtermFlag:period,rgtMgntCd:'',renewalCd:'',ordering:'ASC'};
            for (const [name,value] of Object.entries(values)) {
                const e=f.elements.namedItem(name);
                if (!e) throw new Error('필드 없음: '+name);
                e.value=value;
            }
            f.submit();
        }''',[period,number])
    html = page.content()
    check_blocked(html)
    page.locator('.total_count strong').wait_for(state='attached')
    html = page.content()
    soup,total,size = page_info(html)
    if any(form_value(soup,key)!=value for key,value in
           {'menuId':'C005','longtermFlag':period,'page':str(number)}.items()):
        raise ValueError('응답의 상품분류/보험기간/페이지가 요청과 다릅니다.')
    return html,total,size


def collect_detail(page, record):
    """실제 페이지의 grntPopup을 호출해 팝업·레이어·iframe 모두 확인."""
    context = page.context
    pause_requests(page)
    before = set(context.pages)
    page.evaluate('''code => {
        if (typeof grntPopup !== 'function') throw new Error('grntPopup 함수 없음');
        // 이전 레이어의 보장을 새 상품 상세로 오인하지 않도록 비운다.
        for (const ul of document.querySelectorAll('ul.item_list')) {
            if (!ul.closest('tr[data-prdt-cd]')) ul.replaceChildren();
        }
        grntPopup(code);
    }''',record['상품코드'])
    deadline = time.monotonic()+30
    last_html = ''
    try:
        while time.monotonic() < deadline:
            new_pages = [p for p in context.pages if p not in before]
            candidates = new_pages + [page]
            for candidate in candidates:
                for frame in candidate.frames:
                    if candidate is page and frame is page.main_frame:
                        # 목록의 5개 요약을 상세 전체로 오인하지 않는다.
                        nodes = frame.locator('ul.item_list').all()
                        for node in nodes:
                            if node.locator('xpath=ancestor::tr[@data-prdt-cd]').count():
                                continue
                            if node.is_visible():
                                items = coverages(node.evaluate('(e)=>e.outerHTML'))
                                if items and all(x['지급사유'] for x in items):
                                    return finish_detail(record,items)
                        continue
                    try:
                        last_html = frame.content()
                    except Exception:
                        continue
                    record['_진단HTML'] = last_html
                    check_blocked(last_html)
                    items = coverages(last_html)
                    if items and all(x['지급사유'] for x in items):
                        return finish_detail(record,items)
            page.wait_for_timeout(250)
        record['_진단HTML'] = last_html
        raise RuntimeError('보장 상세를 읽지 못했습니다(차단 또는 팝업 구조 변경).')
    finally:
        for p in list(context.pages):
            if p not in before:
                p.close()


def finish_detail(record, items):
    missing = [x['보장명'] for x in record['_보장']
               if x['보장명'] not in {y['보장명'] for y in items}]
    if missing:
        raise ValueError(f'상세 화면에 목록 보장이 없습니다: {missing}')
    match = re.search(r'총\s*(\d+)\s*종',record['기타사항'])
    advertised = int(match.group(1)) if match else None
    record['비고_보장종수'] = advertised
    record['상세_보장행수'] = len(items)
    record['보장건수일치'] = (advertised == len(items)) if advertised is not None else pd.NA
    warning = ''
    if advertised is not None and len(items) != advertised:
        warning = f'비고 {advertised}종 / 상세 {len(items)}개: 건수 차이 확인 필요'
        print(f'안내 [{record["상품코드"]}]: {warning} (상세 행 전체 저장)')
    record['보장검증메모'] = warning
    record['_보장'] = items
    record['보장수집완료'] = True
    record['보장수집상태'] = '상세 수집: 건수 차이 확인 필요' if warning else '상세 전체'


def _crawl(wide_path,long_path,headless,channel):
    from playwright.sync_api import sync_playwright
    records = []
    wide_path,long_path = Path(wide_path),Path(long_path)
    partial_w = wide_path.with_name(wide_path.stem+'.partial.csv')
    partial_l = long_path.with_name(long_path.stem+'.partial.csv')
    with sync_playwright() as p:
        options = {'headless':headless}
        if channel:
            options['channel'] = channel
        browser = p.chromium.launch(**options)
        context = browser.new_context(locale='ko-KR')
        page = context.new_page()
        try:
            page.goto(URL,wait_until='domcontentloaded',timeout=60000)
            check_blocked(page.content())
            page.locator('#searchForm').wait_for(state='attached')
            for period in PERIODS:
                html,total,size = submit_page(page,period,1)
                seen = set()
                for number in range(1,max(1,math.ceil(total/size))+1):
                    if number != 1:
                        html,current,current_size = submit_page(page,period,number)
                        if (current,current_size)!=(total,size):
                            raise ValueError('수집 도중 총 건수가 변경되었습니다.')
                    batch = parse_products(html,period,number)
                    if len(batch)!=min(size,max(0,total-(number-1)*size)):
                        raise ValueError('페이지 상품 개수가 예상과 다릅니다.')
                    print(f'{PERIODS[period]} {number}페이지: {len(batch)}개 / 총 {total}개')
                    for record in batch:
                        if record['상품코드'] in seen:
                            raise ValueError('페이지에 중복 상품이 있습니다.')
                        seen.add(record['상품코드'])
                        records.append(record)
                        if not record['보장수집완료']:
                            collect_detail(page,record)
                        save_tables(records,partial_w,partial_l)
                if len(seen)!=total:
                    raise ValueError('전체 상품 수집 건수 불일치')
        except Exception as exc:
            folder = wide_path.parent / ('kids_debug_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'list.html').write_text(page.content(),encoding='utf-8')
            if records:
                records[-1]['보장수집상태'] = '중단: '+str(exc)
                for i,r in enumerate(records):
                    if r.get('_진단HTML'):
                        (folder/f'detail_{i}.html').write_text(r['_진단HTML'],encoding='utf-8')
                save_tables(records,partial_w,partial_l)
            raise RuntimeError(f'어린이보험 수집 실패: {exc}\n진단: {folder}\n정상 결과 CSV는 갱신하지 않았습니다.') from exc
        finally:
            browser.close()
    wide,long = save_tables(records,wide_path,long_path)
    print(f'저장 완료: 상품 {len(wide)}행 / 상세 {len(long)}행\n{wide_path}\n{long_path}')
    return wide,long


def get_children_products(wide_output=None,long_output=None,*,headless=False,channel=None):
    """성별·나이 입력 없음. channel='msedge'로 설치된 Edge 사용 가능."""
    wide_output = Path(wide_output) if wide_output else WIDE_PATH
    long_output = Path(long_output) if long_output else wide_output.with_name('어린이_product_longform.csv')
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_crawl,wide_output,long_output,headless,channel).result()


if __name__ == '__main__':
    insurance_product, insurance_product_details = get_children_products()
    insurance_product_features = insurance_product_details.loc[
        insurance_product_details['상세구분'].eq('특성')].copy()
    insurance_product_coverages = insurance_product_details.loc[
        insurance_product_details['상세구분'].eq('보장')].copy()
