"""보험다모아 실손보험(G004): 성별/생년월일 입력 → 개인 조건 조회 → DF/CSV.

VS Code에서 이 파일을 열고 ▶ Python 파일 실행. 터미널 질문에 답하세요.
이미 설치했다면 추가 설치 불필요:
    python -m pip install pandas beautifulsoup4 playwright
    python -m playwright install chromium

함수 사용:
    insurance_product = get_insurance_products('남', '19981116')
    insurance_product = get_insurance_products('여', '1998-11-16', coverage='기본형')

결과: 이 .py 폴더에 다음 두 CSV(UTF-8 BOM)를 저장한다.
    실손의료_product_wideform.csv: 상품·조회조건당 한 행, 특성상세 리스트 제외
    실손의료_product_longform.csv: 상품 특성당 한 행, 한글 필드로 정리
두 테이블의 조회결과ID로 연결한다.
기존 질병보험 insurance_product.csv는 덮어쓰지 않습니다.
보험료는 해당 사이트 조회 조건의 예시 금액이며 가입 승인/최종 견적이 아닙니다.
첨부 실제 결과 HTML로 파싱 검증. 제작 환경에서는 실사이트 연결 제한으로
온라인 통합 실행은 검증하지 못했습니다. 접근 거절을 우회하지 않습니다.
"""

from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from uuid import uuid4

import pandas as pd
from bs4 import BeautifulSoup, Comment

URL = 'https://www.e-insmarket.or.kr/mins/minsInsList.knia?prdtSmlClsCd=G004'
ACTION = '/mins/search.knia'
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = SCRIPT_DIR / '실손의료_product_wideform.csv'
LONG_FILENAME = '실손의료_product_longform.csv'
SEOUL = timezone(timedelta(hours=9))
SEX_NAMES = {'M': '남', 'L': '여'}
CORE_COLUMNS = ['조회일시', '입력생년월일', '성별', '보험나이', '상품코드',
                '회사명', '상품명', '보험료', '보험료_표시', '가입연령', '가입담보']


def normalize_inputs(gender, birth_date):
    aliases = {'남': 'M', '남자': 'M', '남성': 'M', 'M': 'M',
               '여': 'L', '여자': 'L', '여성': 'L', 'F': 'L', 'L': 'L',
               '전체': 'ML', '모두': 'ML', 'ALL': 'ML', 'ML': 'ML'}
    key = str(gender).strip().upper()
    if key not in aliases:
        raise ValueError('성별은 남 / 여 / 전체 중 하나를 입력하세요.')
    value = str(birth_date).strip()
    if not re.fullmatch(r'\d{8}|\d{4}-\d{2}-\d{2}', value):
        raise ValueError('생년월일 형식: 19981116 또는 1998-11-16')
    value = value.replace('-', '')
    try:
        birthday = datetime.strptime(value, '%Y%m%d').date()
    except ValueError as exc:
        raise ValueError('실제로 존재하는 생년월일을 입력하세요.') from exc
    if birthday.year < 1900 or birthday > datetime.now(SEOUL).date():
        raise ValueError('1900년 이후이며 미래가 아닌 생년월일을 입력하세요.')
    return aliases[key], value


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


def parse_result(html, sex_code, birthday, age, coverage_labels, page_number):
    soup = BeautifulSoup(html, 'html.parser')
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    root = soup.select_one('#searchResult') or soup
    table = root.select_one('.list_area table.table_type01')
    count_node = root.select_one('.total_count strong')
    if count_node is None or not re.fullmatch(r'[\d,]+', text(count_node)):
        raise ValueError('검색결과 총 건수 없음: 접속차단 또는 예상과 다른 응답입니다.')
    announced_total = int(text(count_node).replace(',', ''))
    product_rows = table.select('tbody > tr[data-prdt-cd]') if table else []
    if not product_rows and announced_total != 0:
        raise ValueError('상품 건수는 있으나 실제 상품 행을 찾지 못했습니다.')
    if product_rows:
        heads = table.select('thead tr')
        price_sexes = [text(th) for th in heads[-1].find_all('th', recursive=False)
                       if text(th) in ('남', '여')] if heads else []
        if not price_sexes:
            raise ValueError('보험료 남/여 열 헤더를 찾지 못했습니다. 임의로 가격을 배정하지 않습니다.')
    else:
        price_sexes = []
    records = []
    stamp = datetime.now(SEOUL).isoformat(timespec='seconds')
    for row in product_rows:
        company_img = row.select_one('.comp_logo img')
        name = row.select_one('td.prod > p')
        prices = row.select('td.total_cost')
        if company_img is None or name is None or len(prices) != len(price_sexes):
            raise ValueError(f'{row.get("data-prdt-cd")}: 회사/상품/보험료 구조 변경')
        age_cell = prices[-1].find_next_sibling('td')
        cells = row.find_all('td', recursive=False)
        signup_cell = cells[-1]
        remark_cell = row.select_one('td.item_bg')
        remark = remark_cell.select_one('[data-remark], [data-rmk]') if remark_cell else None
        remark_text = html_text(remark.get('data-remark', remark.get('data-rmk', ''))) if remark else text(remark_cell)
        icons_node = row.select_one('[data-iconlist]')
        icons = parse_icon_data(icons_node.get('data-iconlist', '')) if icons_node else []
        for sex_label, price_cell in zip(price_sexes, prices):
            code = 'M' if sex_label == '남' else 'L'
            if code not in sex_code:
                continue
            display = text(price_cell)
            numeric = display.removesuffix('원').strip().replace(',', '')
            amount = int(numeric) if re.fullmatch(r'\d+', numeric) else pd.NA
            record = {
                '조회일시': stamp, '입력생년월일': birthday, '성별': sex_label,
                '보험나이': age, '상품분류코드': 'G004',
                '상품코드': row['data-prdt-cd'], '회사명': company_img.get('alt', ''),
                '상품명': text(name), '보험료': amount, '보험료_표시': display,
                '가격표시여부': pd.notna(amount), '가입연령': text(age_cell),
                '가입담보': coverage_labels, '기타정보': remark_text,
                '가입형태': ' / '.join(text(n) for n in signup_cell.select('span.item') if not n.select_one('button')),
                '가입URL': ' / '.join(dict.fromkeys(n.get('data-url', '').strip() for n in signup_cell.select('[data-url]'))),
                '가입형태_전체표시': text(signup_cell),
                '보험구분': '생명보험' if row.select_one('.tag_bizdiv.life') else '손해보험' if row.select_one('.tag_bizdiv.property') else '',
                '특성상세': icons, '수집페이지': page_number,
                '사이트표시총건수': announced_total, '출처URL': URL,
            }
            for icon in icons:
                title = icon.get('ICON_TITLE', '')
                subtitle = icon.get('ICON_SUB_TITLE', '')
                index = icon.get('ICON_PRICE_INDEX', '')
                record['특성_' + title] = subtitle + (f' / {index}%' if index else '')
                record['특성_' + title + '_설명'] = html_text(icon.get('ICON_DESCRIPTION', ''))
            records.append(record)
    max_page = page_number
    for link in root.select('.paginate a'):
        action = (link.get('onclick', '') + ' ' + link.get('href', ''))
        match = re.search(r'searchPage\(\s*(\d+)\s*\)', action)
        if match:
            max_page = max(max_page, int(match.group(1)))
    current = root.select_one('input[name="page"]')
    if current is not None and int(current.get('value', '0')) != page_number:
        raise ValueError('응답 페이지 불일치: 요청한 결과 페이지와 다릅니다.')
    return records, announced_total, max_page, len(product_rows)


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


def _configure(page, sex_code, birthday, coverage, company_codes, coverage_codes):
    page.locator('#searchForm').wait_for(state='attached', timeout=30000)
    page.wait_for_function("() => window.jQuery && jQuery.isReady && typeof selectChange === 'function' && typeof searchPage === 'function'", timeout=30000)
    companies = page.locator('input[name="insrCmpyCd"]').evaluate_all('(els) => els.map(e => e.value)')
    if company_codes is None:
        _toggle(page, '#cb_insrCmpyAll', True)
        company_codes = companies
    else:
        company_codes = list(dict.fromkeys(company_codes))
        if not company_codes or set(company_codes) - set(companies):
            raise ValueError(f'보험사 코드 오류. 사이트에 있는 코드: {companies}')
        _toggle(page, '#cb_insrCmpyAll', False)
        for code in companies:
            _toggle(page, f'input[name="insrCmpyCd"][value="{code}"]', code in company_codes)
    all_coverages = page.locator('input[name="joinScrtDivCd"]').evaluate_all('(els) => els.map(e => e.value)')
    if coverage_codes is None:
        if coverage == '전체':
            _toggle(page, '#joinScrtDivCdAll', True)
            coverage_codes = all_coverages
        elif coverage == '기본형':
            coverage_codes = ['A', 'B']  # 첨부 G004 폼의 상해급여/질병급여
        else:
            raise ValueError('coverage는 전체 또는 기본형. 직접 선택은 coverage_codes를 지정하세요.')
    coverage_codes = list(dict.fromkeys(coverage_codes))
    if not coverage_codes or set(coverage_codes) - set(all_coverages):
        raise ValueError(f'담보 코드 오류. 사이트에 있는 코드: {all_coverages}')
    if set(coverage_codes) != set(all_coverages):
        _toggle(page, '#joinScrtDivCdAll', False)
        for code in all_coverages:
            _toggle(page, f'input[name="joinScrtDivCd"][value="{code}"]', code in coverage_codes)
    _toggle(page, '#sexAll', False)
    _toggle(page, '#sexM', 'M' in sex_code)
    _toggle(page, '#sexF', 'L' in sex_code)
    date_input = page.locator('#insStartDtPicker')
    date_input.fill(birthday)
    date_input.dispatch_event('change')
    # 직접 age를 추측/덮어쓰지 않고 사이트의 보험나이 계산을 사용한다.
    page.evaluate('() => selectChange()')
    age_text = page.locator('#age').input_value()
    if not re.fullmatch(r'\d+', age_text):
        raise ValueError('사이트에서 보험나이가 계산되지 않았습니다.')
    age = int(age_text)
    labels = page.locator('input[name="joinScrtDivCd"]:checked').evaluate_all('''els => els.map(e => {
        const label = Array.from(document.querySelectorAll('label')).find(l => l.htmlFor === e.id);
        const group = e.closest('ul').parentElement;
        const title = group && group.querySelector(':scope > span > label');
        return `${title ? title.textContent.trim() + ': ' : ''}${label ? label.textContent.trim() : e.value} [${e.value}]`;
    })''')
    return age, ' / '.join(labels), set(company_codes), set(coverage_codes)


def _query(page, number, sex_code, birthday, age, companies, coverages):
    # 같은 URL의 결과 페이지 이동은 Ajax이므로 expect_navigation으로 기다리지 않는다.
    if not page.evaluate('() => validateForm()'):
        raise ValueError('사이트 입력 검증 실패: 기본형 담보를 포함해 선택했는지 확인하세요.')
    # form.method가 POST여도 공통 getHtml()의 Ajax 구현은 GET을 쓸 수 있다.
    # 실제 form.action의 응답을 기다리고 전송 방식은 제한하지 않는다.
    form_action = page.locator('#searchForm').get_attribute('action')
    if not form_action:
        raise ValueError('검색 폼의 action 주소가 없습니다.')
    target = urlparse(urljoin(page.url, form_action))
    def is_search_response(response):
        address = urlparse(response.url)
        return (address.netloc, address.path) == (target.netloc, target.path) and response.request.method in ('GET', 'POST')

    with page.expect_response(is_search_response, timeout=45000) as pending:
        if number == 1:
            page.locator('button[onclick*="searchPage(1)"]').click()
        else:
            page.evaluate('(n) => searchPage(n)', number)
    response = pending.value
    body = response.text()
    if response.status != 200:
        raise RuntimeError(f'상품 검색 요청 거절: HTTP {response.status}')
    # GET: URL query / POST: body. 회사/담보의 반복 필드도 보존한다.
    data = parse_qs(urlparse(response.request.url).query, keep_blank_values=True)
    body_data = parse_qs(response.request.post_data or '', keep_blank_values=True)
    for key, values in body_data.items():
        if key in data and data[key] != values:
            raise ValueError(f'URL과 본문에 서로 다른 조회 조건이 있습니다: {key}')
        data[key] = values
    checks = {'sexDiv': sex_code, 'age': str(age), 'prdtSmlClsCd': 'G004'}
    for key, expected in checks.items():
        if data.get(key) != [expected]:
            raise ValueError(f'전송 조건 불일치: {key}={data.get(key)}, expected={expected}')
    if data.get('startDt', [''])[0].replace('-', '') != birthday:
        raise ValueError('전송된 생년월일이 입력과 다릅니다.')
    if set(data.get('insrCmpyCd', [])) != companies or set(data.get('joinScrtDivCd', [])) != coverages:
        raise ValueError('보험회사/가입담보 전송 조건이 선택한 값과 다릅니다.')
    # 응답만 받았다고 이전 DOM을 읽지 않는다. 새 결과의 페이지 번호까지 확인.
    page.wait_for_function('''n => {
        const r = document.querySelector('#searchResult');
        const p = r && r.querySelector('input[name="page"]');
        return p && Number(p.value) === n && r.querySelector('.total_count strong');
    }''', arg=number, timeout=30000)
    return body


def save_csv(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    export = frame.copy()
    for col in export.columns:
        export[col] = export[col].map(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x)
    temporary = path.with_name(path.name + '.tmp')
    export.to_csv(temporary, index=False, encoding='utf-8-sig')
    temporary.replace(path)


FEATURE_CONTEXT = ['조회결과ID', '상품코드', '회사명', '상품명', '성별', '보험나이',
                   '조회일시', '페이지수집완료']
FEATURE_COLUMNS = FEATURE_CONTEXT + ['특성순번', '특성코드', '특성명', '표시내용',
                  '수치', '수치_원문', '단위', '설명', '출력순서', '기본표시여부', '수치표시여부']


def create_product_tables(frame):
    """원본 조회 DF를 (상품 wide DF, 특성 long DF)로 분리한다.

    특성상세는 Python list[dict] 또는 CSV에서 읽은 JSON 문자열을 지원한다.
    특성이 없는 상품도 wide에 유지한다. 원본 frame을 변경하지 않는다.
    """
    if not frame.empty and '특성상세' not in frame:
        raise ValueError('특성 테이블을 만들려면 원본 특성상세 열이 필요합니다.')
    wide = frame.copy()
    run_id = uuid4().hex
    keys = [f'{run_id}-{i:05d}' for i in range(1, len(wide) + 1)]
    if '조회결과ID' in wide:
        wide = wide.drop(columns='조회결과ID')
    wide.insert(0, '조회결과ID', keys)
    feature_rows, feature_counts = [], []
    for position, (_, product) in enumerate(frame.iterrows()):
        raw = product.get('특성상세')
        if isinstance(raw, str):
            try:
                icons = json.loads(raw) if raw.strip() else []
            except json.JSONDecodeError as exc:
                raise ValueError(f'{product.get("상품코드", "")}: 특성상세 JSON 해석 실패') from exc
        elif isinstance(raw, list):
            icons = raw
        elif raw is None or raw is pd.NA or (isinstance(raw, float) and pd.isna(raw)):
            icons = []
        else:
            raise ValueError(f'{product.get("상품코드", "")}: 특성상세는 리스트여야 합니다.')
        if not isinstance(icons, list) or any(not isinstance(icon, dict) for icon in icons):
            raise ValueError(f'{product.get("상품코드", "")}: 특성상세 원소는 딕셔너리여야 합니다.')
        feature_counts.append(len(icons))
        context = {key: product.get(key, '') for key in FEATURE_CONTEXT}
        context['조회결과ID'] = keys[position]
        for sequence, icon in enumerate(icons, 1):
            source_value = icon.get('ICON_PRICE_INDEX')
            raw_value = '' if source_value is None else str(source_value).strip()
            if raw_value.lower() in ('null', 'undefined', 'nan'):
                raw_value = ''
            has_number_display = str(icon.get('ICON_PRICE_USE', '')).upper() == 'Y'
            feature_rows.append({
                **context,
                '특성순번': sequence,
                '특성코드': icon.get('ICON_CD', ''),
                '특성명': icon.get('ICON_TITLE', ''),
                '표시내용': icon.get('ICON_SUB_TITLE', ''),
                '수치': pd.to_numeric(raw_value.replace(',', ''), errors='coerce'),
                '수치_원문': raw_value,
                # 사이트의 ICON_PRICE_USE=Y인 아이콘은 %로 표시된다.
                '단위': '%' if has_number_display else '',
                '설명': html_text(icon.get('ICON_DESCRIPTION', '')),
                '출력순서': pd.to_numeric(icon.get('ICON_ORDER', ''), errors='coerce'),
                '기본표시여부': str(icon.get('ICON_DEFAULT', '')) == '1',
                '수치표시여부': has_number_display,
            })
    wide = wide.drop(columns=['특성상세'], errors='ignore')
    wide['특성수'] = pd.Series(feature_counts, index=wide.index, dtype='Int64')
    features = pd.DataFrame(feature_rows, columns=FEATURE_COLUMNS)
    features['수치'] = pd.to_numeric(features['수치'], errors='coerce').astype('Float64')
    features['출력순서'] = pd.to_numeric(features['출력순서'], errors='coerce').astype('Int64')
    features['특성순번'] = pd.to_numeric(features['특성순번'], errors='coerce').astype('Int64')
    wide.attrs = dict(frame.attrs)
    return wide, features


def save_product_tables(wide, features, wide_path, long_path):
    if Path(wide_path).resolve() == Path(long_path).resolve():
        raise ValueError('wideform과 longform 저장 경로는 달라야 합니다.')
    save_csv(wide, wide_path)
    save_csv(features, long_path)


def _run(sex_code, birthday, coverage, company_codes, coverage_codes, output, headless, channel,
         long_output=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(f'현재 Python에 playwright가 없습니다. "{sys.executable}" -m pip install playwright') from exc
    records, seen = [], set()
    output = Path(output)
    long_output = Path(long_output) if long_output is not None else output.with_name(LONG_FILENAME)
    last_html = ''
    with sync_playwright() as p:
        launch = {'headless': headless}
        if channel:
            launch['channel'] = channel
        browser = p.chromium.launch(**launch)
        context = browser.new_context(locale='ko-KR', timezone_id='Asia/Seoul')
        page = context.new_page()
        page.set_default_timeout(30000)
        page_errors = []
        request_log = []
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        def record_request(request):
            if request.resource_type in ('xhr', 'fetch', 'document'):
                request_log.append({'method': request.method, 'url': request.url})
                del request_log[:-40]
        page.on('request', record_request)
        try:
            response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f'사이트 접속 오류: HTTP {response.status}')
            age, labels, companies, coverages = _configure(page, sex_code, birthday, coverage, company_codes, coverage_codes)
            print(f'조회조건: {"/".join(SEX_NAMES[s] for s in sex_code)}, {birthday}, 사이트 보험나이 {age}세')
            print('가입담보:', labels)
            number, last_page, announced_total, product_count = 1, 1, None, 0
            while number <= last_page:
                if number > 500:
                    raise RuntimeError('결과 페이지가 500개를 초과했습니다. 페이지 구조를 확인해야 합니다.')
                last_html = _query(page, number, sex_code, birthday, age, companies, coverages)
                batch, reported, linked_last, raw_rows = parse_result(last_html, sex_code, birthday, age, labels, number)
                if announced_total is None:
                    announced_total = reported
                elif announced_total != reported:
                    raise RuntimeError('페이지 이동 중 표시 상품 수가 변경되었습니다.')
                if raw_rows and not batch:
                    raise ValueError('상품은 있지만 선택한 성별의 보험료 열이 없습니다.')
                if not raw_rows and number > 1:
                    raise ValueError('다음 결과 페이지가 비어 있습니다.')
                for record in batch:
                    key = (record['상품코드'], record['성별'])
                    if key in seen:
                        raise ValueError(f'중복 페이지/상품 발견: {key}')
                    seen.add(key)
                    records.append(record)
                product_count += raw_rows
                last_page = max(last_page, linked_last)
                print(f'{number}/{last_page}페이지: 상품 {raw_rows}개, 선택 성별 결과 {len(batch)}행')
                number += 1
                if number <= last_page:
                    time.sleep(1)
            frame = pd.DataFrame(records) if records else pd.DataFrame(columns=CORE_COLUMNS)
            frame['보험료'] = pd.to_numeric(frame['보험료'], errors='coerce').astype('Int64')
            frame['입력생년월일'] = birthday
            frame['가입기준'] = text(BeautifulSoup(page.content(), 'html.parser').select_one('.join_rule'))
            frame['페이지수집완료'] = True
            frame['표시건수일치'] = product_count == announced_total
            frame.attrs.update({'보험나이': age, '입력성별코드': sex_code,
                                '사이트표시총건수': announced_total, '실제상품수': product_count})
            wide, features = create_product_tables(frame)
            save_product_tables(wide, features, output, long_output)
            print(f'상품 테이블 저장: {len(wide)}행 → {output}')
            print(f'특성 테이블 저장: {len(features)}행 → {long_output}')
            if product_count != announced_total:
                print(f'주의: 사이트 표시 {announced_total}건 / 실제 상품 행 {product_count}개. 실제 행을 모두 보존했습니다.')
            return wide, features
        except Exception as exc:
            diagnostic = output.parent / ('mins_debug_' + datetime.now(SEOUL).strftime('%Y%m%d_%H%M%S_%f'))
            diagnostic.mkdir(parents=True, exist_ok=True)
            if last_html:
                (diagnostic / 'last_response.html').write_text(last_html, encoding='utf-8')
            if not page.is_closed():
                (diagnostic / 'page.html').write_text(page.content(), encoding='utf-8')
                try:
                    functions = page.evaluate('''() => Object.fromEntries(['getHtml','search','searchPage'].map(name => [name,
                        typeof window[name] === 'function' ? window[name].toString() : null]))''')
                    (diagnostic / 'site_functions.json').write_text(json.dumps(functions, ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception:
                    pass
            (diagnostic / 'error.json').write_text(json.dumps({'error': str(exc), 'page_errors': page_errors,
                'requests': request_log}, ensure_ascii=False, indent=2), encoding='utf-8')
            if records:
                partial_frame = pd.DataFrame(records)
                partial_frame['페이지수집완료'] = False
                partial_wide, partial_features = create_product_tables(partial_frame)
                save_product_tables(partial_wide, partial_features,
                    output.with_name(output.stem + '.partial.csv'),
                    long_output.with_name(long_output.stem + '.partial.csv'))
            raise RuntimeError(f'조회 실패: {exc}\n진단 HTML/오류 저장: {diagnostic}\n정상 완료된 결과 CSV는 갱신하지 않았습니다.') from exc
        finally:
            browser.close()


def get_insurance_products(gender, birth_date, coverage='전체', *, company_codes=None,
                           coverage_codes=None, output_path=None, headless=False, channel=None,
                           long_output_path=None, return_features=False):
    """성별: 남/여/전체. 생년월일: YYYYMMDD 또는 YYYY-MM-DD.

    coverage: 전체 / 기본형. company_codes=None이면 전체 보험회사.
    coverage_codes는 실제 폼의 담보 코드 목록(예: ['A','B']). 지정 시 coverage보다 우선.
    channel='msedge'이면 설치된 Edge로 실행. 기본은 설치한 Chromium.
    Jupyter 사용 시에도 별도 스레드에서 Playwright를 실행하여 이벤트 루프 충돌 방지.
    두 CSV를 항상 저장한다. 기본 반환값은 상품 wide DF.
    return_features=True이면 (상품 wide DF, 특성 long DF)를 반환한다.
    """
    sex_code, birthday = normalize_inputs(gender, birth_date)
    output = Path(output_path).resolve() if output_path else DEFAULT_CSV
    long_output = Path(long_output_path).resolve() if long_output_path else output.with_name(LONG_FILENAME)
    with ThreadPoolExecutor(max_workers=1) as executor:
        wide, features = executor.submit(_run, sex_code, birthday, coverage, company_codes,
                               coverage_codes, output, headless, channel, long_output).result()
    return (wide, features) if return_features else wide


if __name__ == '__main__':
    print('보험다모아 실손보험 개인 조건 조회 (브라우저 창은 실행 중 닫지 마세요)')
    gender = input('성별 [남 / 여 / 전체]: ').strip()
    birth_date = input('생년월일 [예: 19981116]: ').strip()
    # 기본은 전체 담보. 기본형만 비교하려면 아래 coverage='기본형'으로 변경.
    insurance_product, insurance_product_features = get_insurance_products(
        gender, birth_date, coverage='전체', return_features=True)
    print(insurance_product[['성별', '보험나이', '회사명', '상품명', '보험료']].to_string(index=False))
