"""보험다모아 질병보험 C001 장기·단기 전체 수집.

설치: python -m pip install pandas beautifulsoup4 playwright
      python -m playwright install chromium
실행: VS Code ▶ 또는 python disease_product_crawler.py
저장: 이 .py 폴더에 질병_product_wideform.csv / 질병_product_longform.csv
보장정보·특성정보는 CSV 셀에 JSON 배열로 저장한다.
보장내용 더보기가 있으면 상세 전체를 조회한다. 실패 시 정상 CSV를 덮어쓰지 않는다.
화면에 보장범위지수가 없으면 빈 값이다(보험가격지수와 다른 값).
"""
from __future__ import annotations

import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup, Comment

URL='https://www.e-insmarket.or.kr/guaranteeIns/guaranteeInsList.knia?menuId=C001'
SCRIPT_DIR=Path(__file__).resolve().parent
PERIODS={'Y':'10년이상(장기)','N':'10년미만(단기)'}
REQUEST_INTERVAL=10
WIDE_COLUMNS=['기간','보험구분','회사명','상품명','보장정보','보험료','보장범위지수','가입연령','특성정보','비고','가입방법']
LONG_COLUMNS=['기간','보험구분','회사명','상품명','보장명','보장내용','보장금액']


def clean(value):
    if value is None: return ''
    if hasattr(value,'get_text'): value=value.get_text(' ',strip=True)
    return re.sub(r'\s+',' ',BeautifulSoup(str(value),'html.parser').get_text(' ',strip=True)).strip()


def check_blocked(html):
    lowered=html.lower()
    if 'request flood control' in lowered or 'request rate of the session exceeded' in lowered:
        raise RuntimeError('사이트 요청 빈도 제한으로 차단됐습니다. 재요청하지 않고 중단합니다.')


def parse_icons(raw):
    if not raw: return []
    try: icons=json.loads(raw)
    except json.JSONDecodeError:
        icons=[]
        for block in re.split(r'\},\s*\{',raw.strip().strip('[]')):
            block=block.strip().removeprefix('{').removesuffix('}')
            keys=list(re.finditer(r'(?:^|,\s*)(ICON_[A-Z_]+)=',block))
            item={}
            for i,m in enumerate(keys):
                end=keys[i+1].start() if i+1<len(keys) else len(block)
                val=block[m.end():end].strip()
                item[m[1]]='' if val in ('null','undefined') else val
            if item: icons.append(item)
    if not isinstance(icons,list): raise ValueError('특성정보 배열 형식 오류')
    result=[]
    for icon in icons:
        parts=[clean(icon.get('ICON_DESCRIPTION')),clean(icon.get('ICON_SUB_TITLE'))]
        value=icon.get('ICON_PRICE_INDEX')
        if value not in ('',None): parts.append(f'{value}%')
        result.append({'특성명':clean(icon.get('ICON_TITLE')),
                       '특성내용':' / '.join(p for p in parts if p)})
    return result


def parse_coverages(html):
    soup=BeautifulSoup(html,'html.parser')
    result=[]
    for li in soup.select('ul.item_list > li'):
        name=clean(li.select_one('.kind'))
        if not name: continue
        item={'보장명':name,'보장내용':clean(li.select_one('.src')) or clean(li.get('data-pay-rsn')),
              '보장금액':clean(li.select_one('.cost'))}
        if item not in result: result.append(item)
    # 일부 상세 팝업이 표 형태인 경우.
    if not result:
        for table in soup.select('table'):
            grid=expand_rows(table.select('tr'))
            positions=None
            for cells in grid:
                values=[clean(c) for c in cells]
                compact=[re.sub(r'\s+','',v) for v in values]
                found={}
                for i,v in enumerate(compact):
                    if v in ('보장명','보장내용','담보명','급부명'): found['보장명']=i
                    elif v in ('지급사유','보험금지급사유','보장상세'): found['보장내용']=i
                    elif v in ('보장금액','지급금액','보험금','지급보험금'): found['보장금액']=i
                if len(found)==3: positions=found;continue
                if positions and len(values)>max(positions.values()):
                    item={k:values[i] for k,i in positions.items()}
                    if all(item.values()) and item not in result: result.append(item)
    return result


def expand_rows(rows):
    filled={};result=[]
    for r,row in enumerate(rows):
        c=0
        for cell in row.find_all(['th','td'],recursive=False):
            while (r,c) in filled: c+=1
            for dr in range(int(cell.get('rowspan') or 1)):
                for dc in range(int(cell.get('colspan') or 1)):
                    filled[r+dr,c+dc]=cell
            c+=int(cell.get('colspan') or 1)
        width=max((cc for rr,cc in filled if rr==r),default=-1)+1
        result.append([filled.get((r,i)) for i in range(width)])
    return result


def form_value(soup,name):
    node=soup.select_one(f'#searchForm [name="{name}"]')
    if node is None: raise ValueError(f'검색 폼 필드 없음: {name}')
    if node.name=='select':
        option=node.select_one('option[selected]') or node.select_one('option')
        return option.get('value','') if option else ''
    return node.get('value','')


def parse_page(html,period):
    check_blocked(html)
    soup=BeautifulSoup(html,'html.parser')
    for comment in soup.find_all(string=lambda s:isinstance(s,Comment)): comment.extract()
    if form_value(soup,'menuId')!='C001' or form_value(soup,'longtermFlag')!=period:
        raise ValueError('응답의 상품분류/장단기 조건이 요청과 다릅니다.')
    count=soup.select_one('.total_count strong')
    if count is None: raise ValueError('상품 총 건수가 없습니다.')
    total=int(clean(count).replace(',',''));size=int(form_value(soup,'pageSize'))
    if size<=0: raise ValueError('페이지 크기 오류')
    table=soup.select_one('.list_area table.table_type01')
    if table is None: raise ValueError('상품 표가 없습니다.')
    headers=[re.sub(r'\s+','',clean(x)) for x in expand_rows(table.select('thead > tr'))[-1]]
    scope_col=next((i for i,h in enumerate(headers) if '보장범위' in h),None)
    age_col=next((i for i,h in enumerate(headers) if '가입연령' in h),None)
    if age_col is None: raise ValueError('가입연령 헤더 없음')
    records=[]
    rows=table.select('tbody > tr')
    for row,cells in zip(rows,expand_rows(rows)):
        if not row.has_attr('data-prdt-cd'): continue
        code=row['data-prdt-cd']
        product=row.select_one('td.prod');price=row.select_one('td.total_cost')
        if product is None or price is None: raise ValueError(f'{code}: 회사/보험료 셀 누락')
        img=product.select_one('.comp_logo img');name=product.select_one('p')
        if img is None or name is None: raise ValueError(f'{code}: 회사명/상품명 누락')
        tag=product.select_one('.tag_bizdiv');classes=tag.get('class',[]) if tag else []
        biz='생명' if 'life' in classes else '손해' if any(x in classes for x in ('property','general')) else ''
        if not biz: raise ValueError(f'{code}: 보험구분 미확인')
        icons=row.select_one('[data-iconlist]');remark=row.select_one('[data-remark], [data-rmk]')
        last=row.find_all('td',recursive=False)[-1]
        methods=list(dict.fromkeys(clean(n) for n in last.select('span.item') if not n.select_one('button') and clean(n)))
        premium=clean(price).removesuffix('원').replace(',','').strip()
        if not re.fullmatch(r'\d+',premium): raise ValueError(f'{code}: 보험료를 숫자로 해석할 수 없음: {premium}')
        scope=clean(cells[scope_col]) if scope_col is not None and scope_col<len(cells) else ''
        records.append({'기간':PERIODS[period],'보험구분':biz,'회사명':clean(img.get('alt')),
            '상품명':clean(name),'보장정보':parse_coverages(str(row)),'보험료':int(premium),
            '보장범위지수':float(scope) if re.fullmatch(r'\d+(?:\.\d+)?',scope) else scope,
            '가입연령':clean(cells[age_col]),
            '특성정보':parse_icons(icons.get('data-iconlist','')) if icons else [],
            '비고':clean(remark.get('data-remark',remark.get('data-rmk',''))) if remark else '',
            '가입방법':' / '.join(methods),'_상품코드':code,
            '_상세필요':bool(row.select_one('[onclick*="grntPopup"]'))})
    if total and not records: raise ValueError('상품 행을 읽지 못했습니다.')
    return records,total,size


def make_tables(records):
    wide=pd.DataFrame([{k:r.get(k,'') for k in WIDE_COLUMNS} for r in records],columns=WIDE_COLUMNS)
    long=[]
    for r in records:
        context={k:r[k] for k in LONG_COLUMNS[:4]}
        for coverage in r['보장정보']: long.append({**context,**coverage})
    return wide,pd.DataFrame(long,columns=LONG_COLUMNS)


def save_tables(records,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    wide,long=make_tables(records)
    for frame,name in ((wide,'질병_product_wideform.csv'),(long,'질병_product_longform.csv')):
        export=frame.copy()
        for col in export:
            export[col]=export[col].map(lambda v:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v)
        temp=folder/(name+'.tmp');export.to_csv(temp,index=False,encoding='utf-8-sig');temp.replace(folder/name)
    return wide,long


def submit_page(page,period,number):
    page.wait_for_timeout(REQUEST_INTERVAL*1000)
    with page.expect_navigation(wait_until='domcontentloaded',timeout=60000):
        page.evaluate('''([period,number]) => {
            const f=document.querySelector('#searchForm');
            const values={menuId:'C001',prdtSmlClsCd:'C001',enterType:'A',
                page:String(number),longtermFlag:period,rgtMgntCd:'',renewalCd:'',ordering:'ASC'};
            for (const [name,value] of Object.entries(values)) {
                const el=f.elements.namedItem(name);
                if(!el) throw new Error('필드 없음: '+name);
                el.value=value;
            }
            f.submit();
        }''',[period,number])
    html=page.content();check_blocked(html)
    soup=BeautifulSoup(html,'html.parser')
    if form_value(soup,'page')!=str(number): raise ValueError('응답 페이지 번호 불일치')
    return html


def collect_detail(page,record,debug):
    """실제 페이지 함수로 팝업을 열고 그 응답/렌더링된 내용을 읽는다."""
    context=page.context;before=set(context.pages);responses=[]
    def on_response(response):
        if urlparse(response.url).path=='/grntPopup.knia': responses.append(response)
    page.wait_for_timeout(REQUEST_INTERVAL*1000)
    context.on('response',on_response)
    last_html='';seen_responses=set()
    try:
        page.evaluate('''code => {
            if(typeof grntPopup!=='function') throw new Error('grntPopup 함수 없음');
            for(const ul of document.querySelectorAll('ul.item_list')) {
                if(!ul.closest('tr[data-prdt-cd]')) ul.replaceChildren();
            }
            grntPopup(code);
        }''',record['_상품코드'])
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            for response in list(responses):
                if id(response) in seen_responses: continue
                seen_responses.add(id(response))
                try: last_html=response.text()
                except Exception: continue
                debug['상세HTML']=last_html;check_blocked(last_html)
                if response.status>=400: raise RuntimeError(f'상세 조회 HTTP {response.status}')
                items=parse_coverages(last_html)
                if items and all(x['보장내용'] for x in items): return validate_detail(record,items)
            for p in [n for n in context.pages if n not in before]+[page]:
                for frame in p.frames:
                    if p is page and frame is page.main_frame:
                        for ul in frame.locator('ul.item_list').all():
                            if ul.locator('xpath=ancestor::tr[@data-prdt-cd]').count() or not ul.is_visible(): continue
                            items=parse_coverages(ul.evaluate('(e)=>e.outerHTML'))
                            if items and all(x['보장내용'] for x in items): return validate_detail(record,items)
                        continue
                    try: last_html=frame.content()
                    except Exception: continue
                    debug['상세HTML']=last_html;check_blocked(last_html)
                    items=parse_coverages(last_html)
                    if items and all(x['보장내용'] for x in items): return validate_detail(record,items)
            page.wait_for_timeout(250)
        raise RuntimeError('상세 보장을 읽지 못했습니다. 진단 HTML을 확인하세요.')
    finally:
        context.remove_listener('response',on_response)
        for p in list(context.pages):
            if p not in before: p.close()


def validate_detail(record,items):
    normalize=lambda v:re.sub(r'\s+','',v)
    names={normalize(x['보장명']) for x in items}
    missing=[x['보장명'] for x in record['보장정보'] if normalize(x['보장명']) not in names]
    if missing: raise ValueError(f'목록과 상세의 보장명이 다릅니다: {missing}')
    record['보장정보']=items
    # 비고의 담보 종수는 지급조건별 상세 행수와 다를 수 있어 강제 일치시키지 않는다.


def _run(output_dir,headless,channel):
    from playwright.sync_api import sync_playwright
    records=[];debug={};output_dir=Path(output_dir)
    with sync_playwright() as p:
        options={'headless':headless}
        if channel: options['channel']=channel
        browser=p.chromium.launch(**options);context=browser.new_context(locale='ko-KR');page=context.new_page()
        try:
            page.goto(URL,wait_until='domcontentloaded',timeout=60000)
            check_blocked(page.content());page.locator('#searchForm').wait_for(state='attached')
            for period in PERIODS:
                html=submit_page(page,period,1);debug['목록HTML']=html
                batch,total,size=parse_page(html,period);pages=max(1,math.ceil(total/size));seen=set()
                for number in range(1,pages+1):
                    if number>1:
                        html=submit_page(page,period,number);debug['목록HTML']=html
                        batch,current,current_size=parse_page(html,period)
                        if (current,current_size)!=(total,size): raise ValueError('수집 중 상품 건수/페이지 크기 변경')
                    if len(batch)!=min(size,max(0,total-(number-1)*size)):
                        raise ValueError(f'{PERIODS[period]} {number}페이지 상품 건수 불일치')
                    print(f'{PERIODS[period]} {number}/{pages}페이지: {len(batch)}개 / 총 {total}개')
                    for record in batch:
                        if record['_상품코드'] in seen: raise ValueError('중복 상품 페이지 응답')
                        seen.add(record['_상품코드']);records.append(record)
                        if record['_상세필요']: collect_detail(page,record,debug)
                        if not record['보장정보']: raise ValueError('보장정보 없는 상품')
                        if not all(all(x.get(k) for k in ('보장명','보장내용','보장금액')) for x in record['보장정보']):
                            raise ValueError(f'{record["상품명"]}: 보장명/내용/금액 누락')
                if len(seen)!=total: raise ValueError('전체 상품 건수 불일치')
            wide,long=save_tables(records,output_dir)
            print(f'저장 완료: 상품 {len(wide)}행 / 보장 {len(long)}행\n{output_dir.resolve()}')
            return wide,long
        except Exception as exc:
            folder=output_dir/('disease_debug_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            folder.mkdir(parents=True,exist_ok=True)
            for name,html in debug.items(): (folder/(name+'.html')).write_text(html,encoding='utf-8')
            (folder/'browser.html').write_text(page.content(),encoding='utf-8')
            (folder/'collected.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            raise RuntimeError(f'질병보험 수집 실패: {exc}\n진단 자료: {folder}\n정상 CSV는 갱신하지 않았습니다.') from exc
        finally: browser.close()


def get_disease_products(output_dir=None,*,headless=False,channel=None):
    """장기·단기 전체를 수집해 (상품 DF, 보장 DF) 반환. Edge: channel='msedge'."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_run,output_dir or SCRIPT_DIR,headless,channel).result()


if __name__=='__main__':
    insurance_product,insurance_product_coverages=get_disease_products()
