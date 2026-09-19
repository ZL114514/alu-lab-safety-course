#!/usr/bin/env python3
"""alu_lab - 安徽大学实验室安全准入平台(http://172.17.109.74) 必修课程进度工具.

Scope: learning task only (report course progress like the player does).
No exam logic by design - take the exam yourself in the browser.

Usage:
  python alu.py captcha                 fetch captcha, write 8x upscaled png, print JSON
  python alu.py login <CODE>            login with the pending captcha code, save token
  python alu.py sync                    pull course tree + popup questions -> courses.json
  python alu.py status                  read-only summary: progress / exam thresholds / attempts
  python alu.py run [--limit N] [--course CID] [--retry] [--dry-run] [--speed X] [--no-wait]
                                        complete unfinished courses (idempotent, resumable)
                                        waits each video's real duration before reporting
                                        (popup questions are sent at their ejectTime)

Env overrides: ALU_BASE, ALU_GRAPH, ALU_USER, ALU_PASS, ALU_TOKEN
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('ALU_BASE', 'http://172.17.109.74:8080/jeecg-boot').rstrip('/')
GRAPH = os.environ.get('ALU_GRAPH', '2092848628543557633')  # 基础课程
COURSES_F = os.path.join(HERE, 'courses.json')
STATE_F = os.path.join(HERE, 'state.json')
CAP_JPG = os.path.join(HERE, 'cap.jpg')
CAP_BIG = os.path.join(HERE, 'cap_big.png')

AES_KEY = b'1234567890abcdef1234567890abcdef'  # frontend constant, AES-128-ECB/Pkcs7


def aes_encrypt(password):
    """Same transform the login page does before POSTing."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    data = password.encode('utf-8')
    pad = padding.PKCS7(128).padder()
    data = pad.update(data) + pad.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.ECB()).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    return {} if default is None else default


def save_json(path, obj):
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


class AuthError(Exception):
    pass


def req(path, method='GET', params=None, body=None, retries=4, token=None):
    url = BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    tok = token or load_json(STATE_F).get('token') or os.environ.get('ALU_TOKEN', '')
    if not tok and token != 'anon':
        raise AuthError('not logged in yet: run  python alu.py captcha  then  python alu.py login <CODE>'
                        '   (state.json 里还没 token)')
    last = ''
    for attempt in range(retries):
        r = urllib.request.Request(url, data=data, method=method, headers={
            'X-Access-Token': tok,
            'Content-Type': 'application/json;charset=UTF-8'})
        try:
            txt = urllib.request.urlopen(r, timeout=30).read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'replace')
            if e.code == 401 or 'Token失效' in txt:
                raise AuthError('token expired, run: python alu.py captcha && python alu.py login <CODE>')
            last = 'HTTP %s %s' % (e.code, txt[:160])
        except Exception as e:  # transient network
            last = 'ERR %s' % e
        else:
            try:
                j = json.loads(txt)
            except ValueError:
                last = 'bad json: %s' % txt[:120]
            else:
                if j.get('success') is False and 'Token失效' in str(j.get('message')):
                    raise AuthError('token expired, run: python alu.py captcha && python alu.py login <CODE>')
                return j
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError('%s failed: %s' % (path, last))


def ok(j):
    return (bool(j.get('success')), (j.get('message') or '')[:70])


def cmd_captcha(args):
    key = str(int(time.time() * 1000))
    j = req('/sys/randomImage/%s?_t=%d' % (key, int(time.time())), token='anon')
    raw = base64.b64decode(j['result'].split(',', 1)[1])
    with open(CAP_JPG, 'wb') as fh:
        fh.write(raw)
    from PIL import Image
    im = Image.open(CAP_JPG).convert('RGB')
    im.resize((im.width * 8, im.height * 8), Image.NEAREST).save(CAP_BIG)
    st = load_json(STATE_F)
    st['capkey'] = key
    save_json(STATE_F, st)
    print(json.dumps({'key': key, 'image': CAP_BIG, 'raw': CAP_JPG}, ensure_ascii=False))


def cmd_login(args):
    st = load_json(STATE_F)
    user = os.environ.get('ALU_USER') or st.get('user')
    password = os.environ.get('ALU_PASS') or st.get('password')
    if not (user and password):
        sys.exit('missing credentials: set ALU_USER/ALU_PASS, or fill state.json {user,password}')
    key = os.environ.get('ALU_CAPKEY') or st.get('capkey')
    if not key:
        sys.exit('no pending captcha, run: python alu.py captcha')
    body = {'username': user, 'password': aes_encrypt(password), 'captcha': args.code,
            'checkKey': key, 'remember_me': True}
    j = req('/sys/userLogin', 'POST', body=body, token='anon')
    if not j.get('success'):
        sys.exit('login failed: %s' % j.get('message'))
    st.update({'token': j['result']['token'], 'user': user, 'password': password,
               'capkey': None, 'loginAt': time.strftime('%Y-%m-%d %H:%M:%S')})
    save_json(STATE_F, st)
    info = j['result'].get('userInfo') or {}
    print('login OK  realname=%s  token_len=%d' % (info.get('realname'), len(st['token'])))


def fetch_courses():
    """Rebuild the course table from the server (tree + detail + popup questions)."""
    tree = req('/jcedutec/courseSource/myCourseTypeTree', params={'graphId': GRAPH})['result'] or []
    courses = []
    for cat in tree:
        for node in (cat.get('children') or []):
            cid = node['key']
            title = node.get('title') or ''
            detail = req('/jcedutec/courseSource/queryById', params={'id': cid}).get('result') or {}
            rel = req('/jcedutec/courseSource/queryCourseQuestionRelaByMainId',
                      params={'id': cid}).get('result') or []
            questions = [{'id': q.get('questionId') or q.get('id'), 'kind': str(q.get('kind')),
                          'correct': q.get('correctAnswer'), 'ejectTime': q.get('ejectTime'),
                          'stem': q.get('stem')}
                         for q in rel if q.get('correctAnswer')]
            courses.append({
                'cid': cid,
                'title': title.replace('(学完)', '').replace('(未学)', '').strip(),
                'status': 'done' if '(学完)' in title else 'todo',
                'cat': cat.get('title'), 'catId': cat.get('key'),
                'duration': float(detail.get('duration') or 0),
                'questions': questions,
            })
            time.sleep(0.1)
    save_json(COURSES_F, courses)
    return courses


def load_courses():
    """Loaded table, rebuilt from the server when missing or from an older schema."""
    courses = load_json(COURSES_F, default=[]) or []
    if not courses or any('status' not in c for c in courses):
        courses = fetch_courses()
    return courses


def cmd_sync(args):
    courses = fetch_courses()
    done = sum(1 for c in courses if c['status'] == 'done')
    print('synced %d courses (%d done, %d todo) -> %s' % (len(courses), done, len(courses) - done, COURSES_F))
    for c in courses:
        print('  [%s] %-8s %-44s %5.0fs  q=%d' % (c['status'], c['cat'], c['title'][:44], c['duration'], len(c['questions'])))


def cmd_status(args):
    courses = load_courses()
    done = [c for c in courses if c['status'] == 'done']
    total = sum(c['duration'] for c in courses)
    mmss = lambda s: '%d:%02d' % (s // 60, s % 60)
    print('courses   : %d/%d done, remaining video %s' % (
        len(done), len(courses), mmss(int(sum(c['duration'] for c in courses if c['status'] != 'done')))))
    print('video     : %d items, %s total' % (len(courses), mmss(int(total))))
    for x in (req('/jcedutec/exam/myExamList').get('result') or {}).get('records') or []:
        print('exam gate : %s | need learn>=%s min, rate>=%s%%, %s min, pass>=%s' % (
            x.get('examName'), x.get('learnTime'), x.get('learnRate'), x.get('examTime'), x.get('qualifiedScore')))
        print('attempts  : useCount=%s  best=%s  window %s ~ %s' % (
            x.get('useCount'), x.get('score'), x.get('startTime'), x.get('endTime')))
    me = (req('/students/queryMyInfo').get('result') or {})
    print('student   : %s (%s)  commitment=%s' % (me.get('name'), me.get('code'), me.get('commitmentStatus')))


def fmt_hms(sec):
    sec = max(0, int(sec))
    return '%d:%02d:%02d' % (sec // 3600, (sec % 3600) // 60, sec % 60)


def sleep_until(target, label, quiet=False):
    """Sleep until epoch `target`, heartbeat every 30s (these runs last hours)."""
    last = 0.0
    while True:
        remain = target - time.time()
        if remain <= 0:
            return
        if not quiet and time.time() - last >= 30:
            last = time.time()
            print('      %s  remaining %s' % (label, fmt_hms(remain)), flush=True)
        time.sleep(min(2.0, remain))


def put_question(prog, cid, item):
    """Record a question result, replacing any earlier attempt for the same question."""
    lst = prog.setdefault(cid, {}).setdefault('q', [])
    for i, x in enumerate(lst):
        if x['id'] == item['id']:
            lst[i] = item
            return
    lst.append(item)


def play_course(c, speed, st):
    """Replay the player: visit -> each popup question at its ejectTime -> watch the full
    length -> finishRate -> finish. speed=inf disables all waiting."""
    prog = st.setdefault('progress', {})
    waits = st.setdefault('wait', {})
    cid = c['cid']
    duration = float(c['duration']) / speed
    start = waits.get(cid)
    if start is None:
        start = time.time()
        waits[cid] = start
        visit = ok(req('/jcedutec/courseSource/updateVisits', 'POST', body={'id': cid, 'graphId': GRAPH}))
        save_json(STATE_F, st)
    else:
        visit = (True, 'resumed')
    answered = {x['id'] for x in (prog.get(cid, {}).get('q') or []) if x.get('ok', (False,))[0]}
    bad = []
    for q in sorted(c['questions'], key=lambda q: float(q.get('ejectTime') or 0)):
        if q['id'] in answered:
            continue
        sleep_until(start + min(float(q.get('ejectTime') or 0) / speed, duration),
                    '弹题 %ss' % q.get('ejectTime'))
        option = q['correct'].split(',') if q['kind'] == '3' else q['correct']
        r = ok(req('/jcedutec/courseSource/submitAnswer', 'POST',
                   body={'questionId': q['id'], 'id': cid, 'graphId': GRAPH, 'option': option}))
        put_question(prog, cid, {'id': q['id'], 'option': option, 'ok': r})
        save_json(STATE_F, st)
        if not r[0]:
            bad.append(r[1])
    sleep_until(start + duration, '观看中 %.0fs' % c['duration'])
    res = prog.setdefault(cid, {})
    res['visit'] = visit
    # server parses watchDuration as int; +1 so it clamps to the full video length
    res['rate'] = ok(req('/jcedutec/courseSource/finishRate', 'POST',
                         body={'id': cid, 'graphId': GRAPH, 'watchDuration': int(c['duration']) + 1}))
    res['finish'] = ok(req('/jcedutec/courseSource/finish', 'POST', body={'id': cid, 'graphId': GRAPH}))
    res['ok'] = res['finish'][0] and not bad
    res['watchedAt'] = time.strftime('%Y-%m-%d %H:%M:%S')
    waits.pop(cid, None)
    save_json(STATE_F, st)
    return res, bad, duration


def cmd_run(args):
    courses = load_courses()
    st = load_json(STATE_F)
    prog = st.setdefault('progress', {})
    waits = st.setdefault('wait', {})
    todo = [c for c in courses
            if (args.retry or c['status'] != 'done') and not (prog.get(c['cid'], {}).get('ok') and not args.retry)]
    if args.course:
        todo = [c for c in todo if c['cid'] == args.course]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print('nothing to do (all courses finished; use --retry to force)')
        return
    speed = float('inf') if args.no_wait else max(args.speed, 0.01)
    left = sum(max(0.0, float(c['duration']) / speed - (time.time() - waits.get(c['cid'], time.time())))
               for c in todo)
    print('plan: %d course(s), 预计耗时 %s  (等待: %s)' % (
        len(todo), fmt_hms(left), '关闭(--no-wait)' if args.no_wait else '按视频真实时长 %.2fx' % speed))
    for i, c in enumerate(todo, 1):
        if args.dry_run:
            print('  [dry] %s  %-38s %s  q=%d' % (
                c['cid'], c['title'][:38], fmt_hms(float(c['duration']) / speed), len(c['questions'])))
            continue
        remain = max(0.0, float(c['duration']) / speed - (time.time() - waits.get(c['cid'], time.time())))
        if remain > 60:
            print('%3d/%3d %s  (本门还需等 %s)' % (i, len(todo), c['title'][:40], fmt_hms(remain)), flush=True)
        res, bad, duration = play_course(c, speed, st)
        print('%3d/%3d %-42s wait=%s visit=%s q=%d(bad %d) rate=%s finish=%s' % (
            i, len(todo), c['title'][:42], fmt_hms(duration), res['visit'][0],
            len(res.get('q') or []), len(bad), res['rate'][0], res['finish'][0]), flush=True)
        if bad:
            print('      q errors:', bad[:3], flush=True)
        time.sleep(0.12)
        if i < len(todo):
            print('      下一门预计 %s 后开始' % fmt_hms(sum(
                max(0.0, float(x['duration']) / speed - (time.time() - waits.get(x['cid'], time.time())))
                for x in todo[i:])), flush=True)
    print('done: %d course(s) processed' % len(todo))


def main():
    ap = argparse.ArgumentParser(description='alu_lab course progress tool (no exam logic)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('captcha').set_defaults(func=cmd_captcha)
    p = sub.add_parser('login'); p.add_argument('code'); p.set_defaults(func=cmd_login)
    sub.add_parser('sync').set_defaults(func=cmd_sync)
    sub.add_parser('status').set_defaults(func=cmd_status)
    p = sub.add_parser('run')
    p.add_argument('--limit', type=int); p.add_argument('--course')
    p.add_argument('--retry', action='store_true'); p.add_argument('--dry-run', action='store_true')
    p.add_argument('--speed', type=float, default=1.0,
                   help='wait scaling, debug only (1.0 = real video length)')
    p.add_argument('--no-wait', action='store_true', help='skip all waiting (re-run/testing)')
    p.set_defaults(func=cmd_run)
    args = ap.parse_args()
    try:
        args.func(args)
    except AuthError as e:
        sys.exit(str(e))


if __name__ == '__main__':
    main()
