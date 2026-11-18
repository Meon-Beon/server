"""암보험 D001 표준가입조건: 성별/생년월일 → 갱신형+비갱신형 전체 조회.

VS Code에서 이 파일을 열고 ▶ Python 파일 실행. 성별/생년월일만 입력하세요.
갱신 구분은 항상 두 가지 모두 선택, 보장구분은 암진단(일반암 1,000만원).
함수 사용: insurance_product = get_cancer_products('여', '19990228')
같은 폴더에 암_product_wideform.csv(상품·가격조건당 한 행),
암_product_longform.csv(특성당 한 행)를 저장. 두 테이블은 조회결과ID로 연결.
기존 실손/질병 CSV는 유지.
설치가 필요한 경우 현재 Python 환경에서:
    python -m pip install pandas beautifulsoup4 playwright
    python -m playwright install chromium
첨부 암보험 HTML은 검색 전 빈 결과 화면입니다. 검색 전 폼/구조와
결과 파싱의 합성 사례를 검증했으며 실서버 결과 수집은 검증하지 못했습니다.
보험료는 사이트의 조회 조건에 따른 예시 금액입니다.
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

URL = 'https://www.e-insmarket.or.kr/cancerIns/cancerInsList.knia?prdtSmlClsCd=D001'
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = SCRIPT_DIR / '암_product_wideform.csv'
LONG_FILENAME = '암_product_longform.csv'
SEOUL = timezone(timedelta(hours=9))
SEX_NAMES = {'M':'남', 'L':'여'}
CORE_COLUMNS = ['조회일시','입력생년월일','성별','보험나이','회사명','상품명',
                '상품코드','갱신구분','보장내용','보장금액','보험료','가입연령']

def normalize_inputs(gender, birth_date):
    aliases = {'남': 'M', '남자': 'M', '남성': 'M', 'M': 'M',
               '여': 'L', '여자': 'L', '여성': 'L', 'F': 'L', 'L': 'L'}
    key = str(gender).strip().upper()
    if key not in aliases:
        raise ValueError('성별은 남 또는 여 중 하나만 입력하세요. 전체 선택은 지원하지 않습니다.')
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

def save_csv(frame, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    export = frame.copy()
    for col in export.columns:
        export[col] = export[col].map(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x)
    temporary = path.with_name(path.name + '.tmp')
    export.to_csv(temporary, index=False, encoding='utf-8-sig')
    temporary.replace(path)


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


def parse_cancer_result(html, sex, birthday, age, page_number):
    soup = BeautifulSoup(html, 'html.parser')
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    root = soup.select_one('#searchResult') or soup
    total_node = root.select_one('.total_count strong')
    if total_node is None or not re.fullmatch(r'[\d,]+', text(total_node)):
        raise ValueError('검색결과 건수가 없습니다. 접속차단 또는 결과 구조 변경 여부를 확인하세요.')
    total = int(text(total_node).replace(',', ''))
    # 실제 응답에서는 #searchResult에 탭/총 건수만 있고 상품 표는 형제
    # .list_area에 있다. 상품 헤더를 확인해 문서 전체에서 표를 찾는다.
    candidates = root.select('table.table_type01') + soup.select('table.table_type01')
    table = next((candidate for candidate in candidates
                  if candidate.select_one('thead')
                  and '회사명' in text(candidate.select_one('thead'))
                  and '보험료' in text(candidate.select_one('thead'))), None)
    if table is None:
        raise ValueError('암보험 결과 표가 없습니다.')
    head_rows = table.select('thead > tr')
    header_grid = expand_rows(head_rows)
    if not header_grid:
        raise ValueError('암보험 결과 열 헤더가 없습니다.')
    headers = [re.sub(r'\s+', '', text(cell)) for cell in header_grid[-1]]
    def column(term, required=True):
        indices = [i for i, name in enumerate(headers) if term in name]
        if not indices and required:
            raise ValueError(f'결과 열을 찾지 못했습니다: {term}')
        return indices[0] if indices else None
    product_col, premium_col = column('회사명'), column('보험료')
    age_col, amount_col = column('가입연령'), column('보장금액')
    feature_col, remark_col, signup_col = column('특성'), column('비고'), column('가입형태')
    scope_col = column('보장범위', required=False)
    renewal_col = column('갱신', required=False)
    coverage_cols = [i for i, name in enumerate(headers) if '보장명' in name]
    body_rows = table.select('tbody > tr')
    grid = expand_rows(body_rows)
    groups = {}
    for row, cells in zip(body_rows, grid):
        if len(cells) <= max(product_col, premium_col):
            continue
        product = cells[product_col]
        if product is None:
            continue
        img = product.select_one('.comp_logo img') or product.select_one('img[alt]')
        name_node = product.select_one('p')
        if img is None or name_node is None:
            continue  # 상품 없음 안내용 colspan 행을 상품으로 해석하지 않는다.
        def get(index):
            return cells[index] if index is not None and index < len(cells) else None
        premium = get(premium_col)
        display = text(premium)
        numeric = display.removesuffix('원').strip().replace(',', '')
        amount = int(numeric) if re.fullmatch(r'\d+', numeric) else pd.NA
        name = text(name_node)
        code = row.get('data-prdt-cd', '') or product.get('data-prdt-cd', '') or product.find_parent('tr').get('data-prdt-cd', '')
        if not code:
            # 실제 가입/상세 링크 인자의 상품코드만 사용한다. 임의로 만들지 않는다.
            for node in row.select('[onclick]'):
                match = re.search(r"['\"]([LN]\d{2}[A-Z]\d{3}[A-Za-z0-9]+)['\"]", node.get('onclick', ''))
                if match:
                    code = match.group(1)
                    break
        icons_node = get(feature_col)
        icon_button = icons_node.select_one('[data-iconlist]') if icons_node else None
        icons = parse_icon_data(icon_button.get('data-iconlist', '')) if icon_button else []
        renewal = ''
        renew_data = row.get('data-renew-type', '')
        if renew_data in ('C1', 'C2'):
            renewal = {'C1':'비갱신형', 'C2':'갱신형'}[renew_data]
        renewal_candidates = [text(get(renewal_col))] if renewal_col is not None else []
        renewal_candidates += [text(get(i)) for i in coverage_cols] + [name]
        for candidate in renewal_candidates:
            if renewal:
                break
            if '비갱신' in candidate:
                renewal = '비갱신형'
            elif '갱신형' in candidate or '갱신' in candidate:
                renewal = '갱신형'
        if not renewal and any('보험료갱신형' in icon.get('ICON_TITLE', '') for icon in icons):
            renewal = '갱신형'
        renewal = renewal or '확인필요'
        # 같은 상품의 보험료 셀이 여러 보장 행에 걸친 경우 한 행으로 합친다.
        key = (id(product), id(premium), renewal)
        if key not in groups:
            remark_node = get(remark_col)
            detail = remark_node.select_one('[data-remark], [data-rmk]') if remark_node else None
            signup = get(signup_col)
            remarks = html_text(detail.get('data-remark', detail.get('data-rmk', ''))) if detail else text(remark_node)
            groups[key] = {
                '조회일시': datetime.now(SEOUL).isoformat(timespec='seconds'),
                '입력생년월일': birthday, '성별': SEX_NAMES[sex], '보험나이': age,
                '상품분류코드': 'D001', '가입조건': '표준가입조건',
                '조회갱신구분': '비갱신형+갱신형', '갱신구분': renewal,
                '갱신구분확인완료': renewal != '확인필요',
                '회사명': img.get('alt', ''), '상품명': name, '상품코드': code,
                '보험료': amount, '보험료_표시': display, '가격표시여부': pd.notna(amount),
                '가입연령': text(get(age_col)), '보장범위지수': text(get(scope_col)),
                '선택보장': '암진단(일반암 기준 1,000만원)', '보장상세': [],
                '기타정보': remarks, '특성상세': icons,
                '가입형태': ' / '.join(text(n) for n in signup.select('span.item') if not n.select_one('button')) if signup else '',
                '가입URL': ' / '.join(dict.fromkeys(n.get('data-url', '').strip() for n in signup.select('[data-url]'))) if signup else '',
                '가입형태_전체표시': text(signup), '수집페이지': page_number,
                '사이트표시총건수': total, '출처URL': URL,
            }
            for icon in icons:
                title = icon.get('ICON_TITLE', '')
                index = icon.get('ICON_PRICE_INDEX', '')
                groups[key]['특성_' + title] = icon.get('ICON_SUB_TITLE', '') + (f' / {index}%' if index else '')
                groups[key]['특성_' + title + '_설명'] = html_text(icon.get('ICON_DESCRIPTION', ''))
        record = groups[key]
        coverage_nodes = []
        for index in coverage_cols:
            cell = get(index)
            if cell is not None and all(cell is not old for old in coverage_nodes):
                coverage_nodes.append(cell)
        items = []
        for node in coverage_nodes:
            for li in node.select('ul.item_list > li'):
                items.append({'보장내용': text(li.select_one('.kind')),
                              '지급사유': html_text(li.get('data-pay-rsn', '')),
                              '보장금액': text(li.select_one('.cost')) or text(get(amount_col))})
        if not items:
            names = [text(node) for node in coverage_nodes
                     if text(node) not in ('갱신형', '비갱신형', '갱신', '비갱신')]
            reasons = [html_text(node.get('data-pay-rsn', '')) for node in coverage_nodes if node.has_attr('data-pay-rsn')]
            items = [{'보장내용':' / '.join(names), '지급사유':' / '.join(reasons),
                      '보장금액':text(get(amount_col))}]
        for item in items:
            if item not in record['보장상세']:
                record['보장상세'].append(item)
    records = list(groups.values())
    if total and not records:
        raise ValueError('상품 건수는 있으나 암보험 상품 행을 해석하지 못했습니다.')
    for record in records:
        for field in ('보장내용', '지급사유', '보장금액'):
            record[field] = '\n'.join(f'{i}. {item[field]}' for i, item in enumerate(record['보장상세'], 1))
    last_page = page_number
    for link in soup.select('.paginate a'):
        action = link.get('href', '') + ' ' + link.get('onclick', '')
        match = re.search(r'(?:page|searchPage)\(\s*(\d+)\s*\)', action)
        if match:
            last_page = max(last_page, int(match.group(1)))
    return records, total, last_page


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


def _query(page, number, sex, birthday, age):
    # 페이지 이동 응답에 잘린 날짜가 재출력돼도 원본 입력으로 되돌린다.
    _restore_birthday(page, birthday, expected_age=age)
    action = page.locator('#searchForm').get_attribute('action')
    target = urlparse(urljoin(page.url, action or ''))
    def matches(response):
        address = urlparse(response.url)
        return (address.netloc == target.netloc and response.request.method in ('GET','POST')
                and response.request.resource_type in ('xhr','fetch','document')
                and (address.path == target.path or address.path.startswith('/cancerIns/')))
    # GET/POST와 Ajax/일반 페이지 이동을 모두 지원한다.
    with page.expect_response(matches, timeout=45000) as pending:
        if number == 1:
            page.locator('#searchForm button[onclick*="pageRefresh"]').click()
        else:
            clicked = page.locator('.paginate a').evaluate_all('''(links,n) => {
                const re = new RegExp('(?:page|searchPage)\\\\(\\\\s*' + n + '\\\\s*\\\\)');
                const link = links.find(a => re.test((a.getAttribute('onclick')||'')+' '+(a.getAttribute('href')||'')));
                if (!link) return false;
                link.click(); return true;
            }''', number)
            if not clicked:
                raise ValueError(f'{number}페이지 링크가 없습니다.')
    response = pending.value
    html = response.text()
    if response.status != 200:
        raise RuntimeError(f'암보험 검색 응답 오류: HTTP {response.status}')
    _check_query(response.request, sex, birthday, age)
    # 응답 원문을 파싱하므로 화면 갱신 전의 0건 결과를 다시 읽지 않는다.
    response.finished()
    page.wait_for_load_state('domcontentloaded', timeout=30000)
    if page.evaluate('() => !!window.jQuery'):
        page.wait_for_function('() => !window.jQuery || jQuery.active === 0', timeout=30000)
    return html


FEATURE_CONTEXT = ['조회결과ID','상품코드','회사명','상품명','성별','보험나이',
                   '갱신구분','조회일시','페이지수집완료']
FEATURE_COLUMNS = FEATURE_CONTEXT + ['특성순번','특성코드','특성명','표시내용',
                  '수치','수치_원문','단위','설명','출력순서','기본표시여부','수치표시여부']


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


def _run(sex_code, birthday, output, headless, channel, long_output=None):
    from playwright.sync_api import sync_playwright
    records, seen = [], set()
    output = Path(output)
    long_output = Path(long_output) if long_output is not None else output.with_name(LONG_FILENAME)
    with sync_playwright() as p:
        launch = {'headless':headless}
        if channel:
            launch['channel'] = channel
        browser = p.chromium.launch(**launch)
        context = browser.new_context(locale='ko-KR', timezone_id='Asia/Seoul')
        page = context.new_page()
        events, last_html = [], ''
        page.on('pageerror', lambda error: events.append({'pageerror':str(error)}))
        def log_request(request):
            if request.resource_type in ('xhr','fetch','document'):
                events.append({'method':request.method,'url':request.url})
                del events[:-50]
        page.on('request', log_request)
        try:
            for sex in sex_code:
                response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
                if response and response.status >= 400:
                    raise RuntimeError(f'암보험 페이지 접근 오류: HTTP {response.status}')
                age = _configure(page, sex, birthday)
                print(f'조회: {SEX_NAMES[sex]} / {birthday} / 보험나이 {age}세 / 갱신형+비갱신형 / 암진단')
                number, last_page, reported_first = 1, 1, None
                while number <= last_page:
                    if number > 500:
                        raise ValueError('페이지가 500개를 넘었습니다. 페이지 구조를 확인해야 합니다.')
                    last_html = _query(page, number, sex, birthday, age)
                    batch, reported, linked_last = parse_cancer_result(last_html, sex, birthday, age, number)
                    if reported_first is None:
                        reported_first = reported
                    elif reported != reported_first:
                        raise ValueError('페이지 이동 중 표시 상품 수가 변경되었습니다.')
                    if not batch and number > 1:
                        raise ValueError('다음 페이지가 비어 있습니다.')
                    # 같은 상품도 갱신형/비갱신형 또는 보험료 조건이 다르면 별도 행.
                    page_keys = {(sex,r['회사명'],r['상품명'],r['상품코드'],r['갱신구분'],r['보험료_표시']) for r in batch}
                    if seen & page_keys:
                        raise ValueError('이전 페이지와 같은 상품/조건이 반복됩니다.')
                    seen.update(page_keys)
                    records.extend(batch)
                    last_page = max(last_page, linked_last)
                    print(f'{SEX_NAMES[sex]} {number}/{last_page}페이지: {len(batch)}개 가격 조건')
                    number += 1
                    if number <= last_page:
                        time.sleep(1)
            frame = pd.DataFrame(records) if records else pd.DataFrame(columns=CORE_COLUMNS)
            frame['보험료'] = pd.to_numeric(frame['보험료'], errors='coerce').astype('Int64')
            frame['페이지수집완료'] = True
            frame['가입기준'] = text(BeautifulSoup(page.content(),'html.parser').select_one('.join_rule'))
            wide, features = create_product_tables(frame)
            save_product_tables(wide, features, output, long_output)
            print(f'상품 테이블 저장: {len(wide)}행 → {output}')
            print(f'특성 테이블 저장: {len(features)}행 → {long_output}')
            if records and not frame['갱신구분확인완료'].all():
                print('일부 행은 갱신구분을 확인하지 못했습니다. 갱신구분=확인필요인 행을 확인하세요.')
            return wide, features
        except Exception as exc:
            folder = output.parent / ('cancer_debug_' + datetime.now(SEOUL).strftime('%Y%m%d_%H%M%S_%f'))
            folder.mkdir(parents=True, exist_ok=True)
            if last_html:
                (folder/'last_response.html').write_text(last_html,encoding='utf-8')
            if not page.is_closed():
                (folder/'page.html').write_text(page.content(),encoding='utf-8')
                try:
                    functions = page.evaluate("() => Object.fromEntries(['getHtml','search','page','pageRefresh'].map(k => [k,typeof window[k]==='function'?window[k].toString():null]))")
                    (folder/'site_functions.json').write_text(json.dumps(functions,ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception:
                    pass
            (folder/'error.json').write_text(json.dumps({'error':str(exc),'events':events},ensure_ascii=False,indent=2),encoding='utf-8')
            if records:
                partial = pd.DataFrame(records)
                partial['페이지수집완료'] = False
                partial_wide, partial_features = create_product_tables(partial)
                save_product_tables(partial_wide, partial_features,
                    output.with_name(output.stem+'.partial.csv'),
                    long_output.with_name(long_output.stem+'.partial.csv'))
            raise RuntimeError(f'암보험 조회 실패: {exc}\n진단 자료: {folder}\n기존 정상 결과 CSV는 갱신하지 않았습니다.') from exc
        finally:
            browser.close()


def get_cancer_products(gender, birth_date, *, output_path=None, headless=False, channel=None,
                        long_output_path=None, return_features=False):
    """성별 남 또는 여 중 하나, 생년월일 YYYYMMDD 또는 YYYY-MM-DD.

    표준가입조건 D001만 조회. 갱신 구분은 항상 C1+C2, 보장은 암진단.
    입력한 한 성별만 조회한다. channel='msedge'도 사용 가능.
    두 CSV를 항상 저장한다. 기본 반환값은 상품 wide DF.
    return_features=True이면 (상품 wide DF, 특성 long DF)를 반환한다.
    """
    sex_code,birthday = normalize_inputs(gender,birth_date)
    output = Path(output_path).resolve() if output_path else DEFAULT_CSV
    long_output = Path(long_output_path).resolve() if long_output_path else output.with_name(LONG_FILENAME)
    with ThreadPoolExecutor(max_workers=1) as executor:
        wide, features = executor.submit(_run,sex_code,birthday,output,headless,channel,long_output).result()
    return (wide, features) if return_features else wide


if __name__ == '__main__':
    print('보험다모아 암보험 조회: 표준가입조건 / 갱신형+비갱신형 / 암진단 1,000만원')
    gender = input('성별 [남 / 여]: ').strip()
    birth_date = input('생년월일 [예: 19990228]: ').strip()
    insurance_product, insurance_product_features = get_cancer_products(gender,birth_date,return_features=True)
    print(insurance_product[['성별','보험나이','회사명','상품명','갱신구분','보험료']].to_string(index=False))

