"""암보험 전체 페이지 -> 상품 wideform / 보장 longform.
고정 조건: 표준가입조건 / 남성 / 1981-11-16 / 갱신형+비갱신형 / 암진단.
설치: python -m pip install pandas beautifulsoup4 playwright
      python -m playwright install chromium
실행: VS Code ▶ 또는 python cancer_product_crawler.py
두 CSV는 이 .py와 같은 폴더에 UTF-8 BOM으로 저장한다.
보장정보/특성정보는 CSV 셀에 JSON 배열로 저장한다(json.loads로 복원).
"""
from __future__ import annotations
import json
import math
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from bs4 import BeautifulSoup, Comment

URL='https://www.e-insmarket.or.kr/cancerIns/cancerInsList.knia?prdtSmlClsCd=D001'
SCRIPT_DIR=Path(__file__).resolve().parent
SEOUL=timezone(timedelta(hours=9))
SEX_NAMES={'M':'남','L':'여'}
BIRTHDAY='19811116'
SEX='M'
WIDE_COLUMNS=['번호','보험구분','회사명','상품명','보장정보','보험료','보장범위지수','가입연령','특성정보','비고','가입방법']
LONG_COLUMNS=['보험구분','회사명','상품명','보장명','보장내용','보장금액']

def text(node):
    if node is None:
        return ''
    value = node.get_text(' ', strip=True) if hasattr(node, 'get_text') else str(node)
    return re.sub(r'\s+', ' ', value).strip()

def html_text(value):
    return text(BeautifulSoup(value or '', 'html.parser'))

def parse_icon_data(raw):
    # Java Map 문자열: 설명 속 쉼표는 유지하고 ICON_키 경계로만 분리.
    result = []
    for block in re.split(r'\},\s*\{', raw.strip().strip('[]')):
        block = block.strip().removeprefix('{').removesuffix('}')
        matches = list(re.finditer(r'(?:^|,\s*)(ICON_[A-Z_]+)=', block))
        item = {}
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
            value = block[match.end():end].strip()
            item[match.group(1)] = '' if value in ('null', 'undefined') else value
        if item:
            result.append(item)
    return result

def _toggle(page, selector, checked):
    # 화면의 실제 checkbox.click()을 호출해 checkAll 등 기존 onclick도 실행한다.
    control = page.locator(selector)
    if control.count() != 1:
        raise ValueError(f'선택 컨트롤이 없거나 중복입니다: {selector}')
    control.evaluate('''(el, wanted) => {
        if (el.checked !== wanted) {
            if (el.disabled) throw new Error('선택이 비활성화되어 있습니다: ' + el.id);
            el.click();
        }
        if (el.checked !== wanted) throw new Error('선택 상태가 변경되지 않았습니다.');
    }''', checked)

def expand_rows(rows):
    """HTML 셀 자체를 유지하며 rowspan/colspan을 펼친다."""
    filled, expanded = {}, []
    for r, row in enumerate(rows):
        c = 0
        for cell in row.find_all(['th', 'td'], recursive=False):
            while (r, c) in filled:
                c += 1
            for dr in range(int(cell.get('rowspan') or 1)):
                for dc in range(int(cell.get('colspan') or 1)):
                    filled[r + dr, c + dc] = cell
            c += int(cell.get('colspan') or 1)
        width = max((cc for rr, cc in filled if rr == r), default=-1) + 1
        expanded.append([filled.get((r, col)) for col in range(width)])
    return expanded

def _restore_birthday(page, birthday, expected_age=None):
    """달력의 YYYY-MM-DD를 서버/maxlength가 기대하는 YYYYMMDD로 고정한다."""
    field = page.locator('#insStartDtPicker')
    field.wait_for(state='attached', timeout=30000)
    field.fill(birthday)
    field.dispatch_event('change')
    if field.input_value() != birthday:
        raise ValueError('생년월일 입력칸이 8자리 YYYYMMDD 값을 유지하지 못했습니다.')
    if expected_age is not None:
        page.wait_for_function('''expected => {
            const el = document.querySelector('#age');
            return el && el.value === String(expected);
        }''', arg=expected_age, timeout=15000)

def _configure(page, sex, birthday):
    page.locator('#searchForm').wait_for(state='attached', timeout=30000)
    page.wait_for_function("() => window.jQuery && jQuery.isReady", timeout=30000)
    category = page.locator('#searchForm [name="prdtSmlClsCd"]').input_value()
    if category != 'D001':
        raise ValueError(f'표준가입조건(D001) 화면이 아닙니다: {category}')
    # 상호배타 클릭 핸들러가 있을 수 있으므로 선택할 성별부터 클릭한다.
    selected, other = ('#sexM','#sexL') if sex == 'M' else ('#sexL','#sexM')
    _toggle(page, selected, True)
    _toggle(page, other, False)
    _toggle(page, '#renewTypeA', True)
    _toggle(page, '#renewTypeB', True)
    diagnosis = page.locator('#indemnityTypeA')
    # 암진단은 사이트에서 disabled+checked로 고정되어 있다. 비활성 상태를 임의로 해제하지 않는다.
    if not diagnosis.is_checked():
        _toggle(page, '#indemnityTypeA', True)
    page.locator('#insStartDtPicker').fill(birthday)
    page.locator('#insStartDtPicker').dispatch_event('change')
    # 실제 달력 선택 콜백을 호출한다. 이름을 모르는 나이 계산 함수를 추측하지 않는다.
    page.evaluate('''dob => {
        const el = document.querySelector('#insStartDtPicker');
        const jq = window.jQuery;
        if (jq && jq.fn.datepicker && jq(el).hasClass('hasDatepicker')) {
            const date = new Date(Number(dob.slice(0,4)), Number(dob.slice(4,6))-1, Number(dob.slice(6,8)));
            jq(el).datepicker('setDate', date);
            const callback = jq(el).datepicker('option', 'onSelect');
            if (typeof callback === 'function') callback.call(el, el.value, jq(el).data('datepicker'));
            jq(el).trigger('change');
        }
    }''', birthday)
    # datepicker setDate는 하이픈을 포함한 10자리를 쓴다. 이 값은 다음
    # 응답에서 maxlength=8에 맞춰 잘릴 수 있으므로 계산 후 8자리로 복원한다.
    _restore_birthday(page, birthday)
    page.wait_for_function("() => /^\\d+$/.test(document.querySelector('#age').value)", timeout=15000)
    age = int(page.locator('#age').input_value())
    return age

def _check_query(request, sex, birthday, age):
    data = parse_qs(urlparse(request.url).query, keep_blank_values=True)
    for key, values in parse_qs(request.post_data or '', keep_blank_values=True).items():
        if key in data and data[key] != values:
            raise ValueError(f'URL과 본문의 조회 조건 불일치: {key}')
        data[key] = values
    expected_sex = '1' if sex == 'M' else '2'
    transmitted_sex = data.get('sex', data.get('sexDiv', []))
    if transmitted_sex != [expected_sex]:
        raise ValueError(f'성별 전송 오류: {transmitted_sex}, 예상 {expected_sex}')
    if data.get('age') != [str(age)]:
        raise ValueError('전송된 보험나이가 화면에서 계산한 나이와 다릅니다.')
    if data.get('birthday') != [birthday]:
        raise ValueError(f'생년월일은 8자리로 전송해야 합니다: 실제 {data.get("birthday")}, 예상 {birthday}')
    if data.get('prdtSmlClsCd') != ['D001']:
        raise ValueError('표준가입조건 D001이 전송되지 않았습니다.')
    renew_values = ' '.join(v for key, values in data.items() if 'renew' in key.lower() for v in values)
    if 'C1' not in renew_values or 'C2' not in renew_values:
        raise ValueError(f'갱신형/비갱신형 두 조건이 모두 전송되지 않았습니다: {renew_values}')
    if 'indemnityTypeA' in data and data['indemnityTypeA'] != ['1']:
        raise ValueError('암진단 보장 조건이 다르게 전송되었습니다.')
    return data

def check_response(html):
    if 'request flood control' in html.lower() or 'request rate of the session exceeded' in html.lower():
        raise RuntimeError('사이트 요청 빈도 제한으로 차단됐습니다. 재요청하지 않고 중단합니다.')


def parse_page(html, page_number=1):
    check_response(html)
    soup=BeautifulSoup(html,'html.parser')
    for comment in soup.find_all(string=lambda s:isinstance(s,Comment)):
        comment.extract()
    count=soup.select_one('.total_count strong')
    if count is None: raise ValueError('검색 결과 총 건수가 없습니다.')
    total=int(text(count).replace(',',''))
    table=next((t for t in soup.select('table.table_type01')
                if '회사명' in text(t.select_one('thead')) and '보험료' in text(t.select_one('thead'))),None)
    if table is None: raise ValueError('상품 결과 표를 찾지 못했습니다.')
    headers=[re.sub(r'\s+','',text(c)) for c in expand_rows(table.select('thead > tr'))[-1]]
    def col(term):
        found=[i for i,h in enumerate(headers) if term in h]
        if not found: raise ValueError(f'필수 표 헤더 없음: {term}')
        return found[0]
    ci,pi,ai=col('회사명'),col('보험료'),col('보장금액')
    ni,si,ji,fi,ri,mi=col('번호'),col('보장범위'),col('가입연령'),col('특성'),col('비고'),col('가입형태')
    coverage_cols=[i for i,h in enumerate(headers) if '보장명' in h]
    trs=table.select('tbody > tr');grid=expand_rows(trs)
    groups={}
    for tr,cells in zip(trs,grid):
        def cell(i): return cells[i] if i<len(cells) else None
        product=cell(ci)
        if product is None: continue
        logo=product.select_one('.comp_logo img')
        name=product.select_one('p')
        if logo is None or name is None: continue
        # 동일한 rowspan 상품 셀의 보장들을 한 상품으로 묶는다.
        key=id(product)
        if key not in groups:
            tag=product.select_one('.tag_bizdiv')
            classes=tag.get('class',[]) if tag else []
            biz='생명' if 'life' in classes else '손해' if any(c in classes for c in ('property','general')) else ''
            if not biz: raise ValueError(f'보험구분을 확인할 수 없습니다: {text(name)}')
            premium=text(cell(pi)).removesuffix('원').replace(',','').strip()
            if not re.fullmatch(r'\d+',premium):
                raise ValueError(f'숫자가 아닌 보험료: {premium!r}')
            icon_btn=cell(fi).select_one('[data-iconlist]') if cell(fi) else None
            features=[]
            for icon in parse_icon_data(icon_btn.get('data-iconlist','') if icon_btn else ''):
                desc=html_text(icon.get('ICON_DESCRIPTION',''))
                subtitle=html_text(icon.get('ICON_SUB_TITLE',''))
                index=icon.get('ICON_PRICE_INDEX','')
                details=[v for v in (desc,subtitle,f'{index}%' if index else '') if v]
                features.append({'특성명':html_text(icon.get('ICON_TITLE','')),'특성내용':' / '.join(details)})
            remark=cell(ri).select_one('[data-remark]') if cell(ri) else None
            signup=cell(mi)
            methods=list(dict.fromkeys(text(x) for x in signup.select('span.item')
                                      if not x.select_one('button') and text(x))) if signup else []
            scope=text(cell(si))
            number=text(cell(ni))
            groups[key]={'번호':int(number) if number.isdigit() else number,
                '보험구분':biz,'회사명':logo.get('alt','').strip(),'상품명':text(name),
                '보장정보':[],'보험료':int(premium),
                '보장범위지수':float(scope) if re.fullmatch(r'\d+(?:\.\d+)?',scope) else scope,
                '가입연령':text(cell(ji)),'특성정보':features,
                '비고':html_text(remark.get('data-remark','')) if remark else text(cell(ri)),
                '가입방법':' / '.join(methods)}
        record=groups[key]
        # 같은 상품 셀에 다른 가격 조건이 들어오면 임의로 한 보험료를 선택하지 않는다.
        premium_now=text(cell(pi)).removesuffix('원').replace(',','').strip()
        if premium_now!=str(record['보험료']):
            raise ValueError(f'{record["상품명"]}: 한 상품에 여러 보험료가 있습니다. 가격 조건 구조 확인 필요')
        visited=set()
        for i in coverage_cols:
            node=cell(i)
            if node is None or id(node) in visited: continue
            visited.add(id(node))
            for li in node.select('ul.item_list > li'):
                coverage={'보장명':text(li.select_one('.kind')),
                          '보장내용':html_text(li.get('data-pay-rsn','')),
                          '보장금액':text(li.select_one('.cost')) or text(cell(ai))}
                if not coverage['보장명']: continue
                if not coverage['보장내용']:
                    raise ValueError(f'{record["상품명"]}/{coverage["보장명"]}: 지급사유 누락')
                if coverage not in record['보장정보']: record['보장정보'].append(coverage)
    records=list(groups.values())
    for record in records:
        if not record['보장정보']: raise ValueError(f'{record["상품명"]}: 보장정보를 읽지 못했습니다.')
    if total and not records: raise ValueError('상품 건수는 있지만 상품 행을 읽지 못했습니다.')
    last=page_number
    for a in soup.select('.paginate a'):
        action=(a.get('href','')+' '+a.get('onclick',''))
        m=re.search(r'(?:page|searchPage)\(\s*(\d+)\s*\)',action)
        if m: last=max(last,int(m[1]))
    return records,total,last


def make_tables(records):
    wide=pd.DataFrame(records,columns=WIDE_COLUMNS)
    long=[]
    for r in records:
        common={key:r[key] for key in ('보험구분','회사명','상품명')}
        for item in r['보장정보']: long.append({**common,**item})
    return wide,pd.DataFrame(long,columns=LONG_COLUMNS)


def save_tables(records, folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    wide,long=make_tables(records)
    for frame,name in ((wide,'암_product_wideform.csv'),(long,'암_product_longform.csv')):
        export=frame.copy()
        for column in export:
            export[column]=export[column].map(lambda x:json.dumps(x,ensure_ascii=False) if isinstance(x,(list,dict)) else x)
        temporary=folder/(name+'.tmp')
        export.to_csv(temporary,index=False,encoding='utf-8-sig')
        temporary.replace(folder/name)
    return wide,long


def query_page(page, number, age):
    _restore_birthday(page,BIRTHDAY,expected_age=age)
    time.sleep(8)
    target=urlparse(urljoin(page.url,page.locator('#searchForm').get_attribute('action') or ''))
    def matches(response):
        address=urlparse(response.url)
        return (address.netloc==target.netloc and response.request.method in ('GET','POST')
            and response.request.resource_type in ('xhr','fetch','document')
            and (address.path==target.path or address.path.startswith('/cancerIns/')))
    with page.expect_response(matches,timeout=60000) as pending:
        if number==1:
            page.locator('#searchForm button[onclick*="pageRefresh"]').click()
        else:
            # 첨부 HTML의 실제 페이지 링크를 클릭한다.
            clicked=page.locator('.paginate a').evaluate_all(r'''(links, wanted) => {
                const link=links.find(a => {
                    const action=(a.getAttribute('onclick')||'')+' '+(a.getAttribute('href')||'');
                    const m=action.match(/(?:page|searchPage)\(\s*(\d+)\s*\)/);
                    return m && Number(m[1])===wanted;
                });
                if (!link) return false;
                link.click(); return true;
            }''',number)
            if not clicked: raise ValueError(f'{number}페이지 링크를 찾지 못했습니다.')
    response=pending.value
    html=response.text();check_response(html)
    if response.status!=200: raise RuntimeError(f'조회 실패: HTTP {response.status}')
    _check_query(response.request,SEX,BIRTHDAY,age)
    response.finished()
    page.wait_for_load_state('domcontentloaded')
    if page.evaluate('() => !!window.jQuery'):
        page.wait_for_function('() => !window.jQuery || jQuery.active===0',timeout=30000)
    return html


def _run(output_dir, headless, channel):
    from playwright.sync_api import sync_playwright
    records=[];last_html='';output_dir=Path(output_dir)
    with sync_playwright() as p:
        options={'headless':headless}
        if channel: options['channel']=channel
        browser=p.chromium.launch(**options)
        page=browser.new_page(locale='ko-KR')
        try:
            page.goto(URL,wait_until='domcontentloaded',timeout=60000)
            age=_configure(page,SEX,BIRTHDAY)
            print(f'조회: 표준가입 / 남 / 1981-11-16 / 보험나이 {age}세 / 갱신형+비갱신형 / 암진단')
            number=1;last_page=1;expected=None;seen=set()
            while number<=last_page:
                last_html=query_page(page,number,age)
                batch,total,pages=parse_page(last_html,number)
                if expected is None: expected=total
                elif total!=expected: raise ValueError('수집 도중 총 상품 건수가 변경됐습니다.')
                last_page=max(last_page,pages)
                for r in batch:
                    if r['번호'] in seen: raise ValueError(f'페이지 중복 상품 번호: {r["번호"]}')
                    seen.add(r['번호']);records.append(r)
                print(f'{number}/{last_page}페이지: {len(batch)}개 상품 / 누적 {len(records)}개 / 총 {total}개')
                if number<last_page and not batch: raise ValueError('중간 페이지가 비어 있습니다.')
                number+=1
            if len(records)!=expected:
                raise ValueError(f'전체 건수 불일치: 사이트 {expected}개 / 수집 {len(records)}개')
            wide,long=save_tables(records,output_dir)
            print(f'저장 완료: 상품 {len(wide)}행 / 보장 {len(long)}행\n{output_dir.resolve()}')
            return wide,long
        except Exception as exc:
            folder=output_dir/('cancer_debug_'+datetime.now(SEOUL).strftime('%Y%m%d_%H%M%S_%f'))
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'response.html').write_text(last_html,encoding='utf-8')
            (folder/'browser.html').write_text(page.content(),encoding='utf-8')
            # 실패 때는 요청한 정식 CSV를 덮어쓰지 않고 진단 폴더 안에 진행 데이터만 보존.
            (folder/'collected.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            raise RuntimeError(f'암보험 수집 실패: {exc}\n진단 자료: {folder}\n정상 결과 CSV는 갱신하지 않았습니다.') from exc
        finally:
            browser.close()


def get_cancer_products(output_dir=None, *, headless=False, channel=None):
    """(상품 DataFrame, 보장 DataFrame) 반환. channel='msedge'로 Edge 사용 가능."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_run,output_dir or SCRIPT_DIR,headless,channel).result()


if __name__=='__main__':
    insurance_product,insurance_product_coverages=get_cancer_products()
