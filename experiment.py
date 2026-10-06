"""Reproduce the vacancy experiment from one local, canonical dataset."""
import argparse
import hashlib
import json
import math
import os
import platform
import random
import tempfile
from collections import defaultdict
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent
DATASET = BASE / 'data' / 'dataset.json'
OUTPUT = BASE / 'artifacts'
DETECTORS = ('aiornot', 'pangram', 'gptzero')
AGG_ALL = 'все агентства и компании, кроме lenkep'
AGG_UNKNOWN = 'разное'
AGGREGATES = {AGG_ALL, AGG_UNKNOWN}
DISPLAY_NAMES = {'agency:eql': 'agency:eql aka rivni'}
COMPARISON_CHARTS = ('agencies_chart.png', 'companies_chart.png', 'ai_and_aggregates_chart.png')
CHART_THEME = {'primary': '#f59e0b', 'strong': '#c45a00', 'text': '#1f2f3f',
               'secondary': '#5b6170', 'border': '#ebe3d8', 'background': '#ffffff'}
URL_RE = re.compile(r'https?://\S+|www\.\S+|(?<!\w)t\.me/\S+', re.IGNORECASE)
LETTER_RE = re.compile(r'[^\W\d_]', re.UNICODE)


def sample_id(row):
    payload = json.dumps([row['text'], row['published_in']],
                         ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def cleaned_text(text):
    text = re.sub(r'<[^<>]+>', ' ', text)
    text = URL_RE.sub(' ', text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return re.sub(r' {2,}', ' ', text).strip()


def checkable(row, config):
    text = cleaned_text(row['text'])
    words = sum(bool(LETTER_RE.search(token)) for token in text.split())
    return len(text) >= config['min_chars'] and words >= config['min_words']


def order_key(row):
    # Missing historical publication dates remain unknown and sort last.
    return row['published_at'], row['published_in'], row['id']


def load_dataset():
    payload = json.loads(DATASET.read_text(encoding='utf-8'))
    if payload.get('schema_version') != 1:
        raise ValueError('Unsupported dataset schema version')
    if set(payload['detectors']) != set(DETECTORS):
        raise ValueError('Dataset must describe exactly the three recorded detectors')
    config = payload['experiment']
    limits = {'per_tag', 'min_tag_size', 'unknown_checkable', 'min_chars', 'min_words'}
    if set(config) != limits | {'market_excluded_owner'}:
        raise ValueError('Invalid experiment configuration')
    if any(type(config[key]) is not int or config[key] <= 0 for key in limits):
        raise ValueError('Experiment limits must be positive integers')
    if not isinstance(config['market_excluded_owner'], str):
        raise ValueError('Invalid market exclusion')
    rows, seen = payload['vacancies'], set()
    if not isinstance(rows, list) or not rows:
        raise ValueError('Dataset contains no vacancies')
    for row in rows:
        if set(row) != {'id', 'text', 'owner', 'published_at', 'published_in', 'results'}:
            raise ValueError('Invalid vacancy fields')
        if any(not isinstance(row[key], str) for key in ['id', 'text', 'owner', 'published_at', 'published_in']):
            raise ValueError('Vacancy fields must be strings')
        if not all(row[key].strip() for key in ['text', 'owner', 'published_in']):
            raise ValueError('Vacancy text, owner and publication source must be populated')
        sid = row['id']
        if sid in seen:
            raise ValueError(f'Duplicate vacancy: {sid}')
        seen.add(sid)
        if sid != sample_id(row):
            raise ValueError(f'Content ID mismatch: {sid}')
        if row['published_at']:
            date = datetime.strptime(row['published_at'], '%Y-%m-%dT%H:%M:%SZ')
            if date.strftime('%Y-%m-%dT%H:%M:%SZ') != row['published_at']:
                raise ValueError(f'Publication date must be canonical UTC: {sid}')
        results = row['results']
        if not isinstance(results, dict) or not set(results) <= set(DETECTORS):
            raise ValueError(f'Unknown detector result: {sid}')
        for detector, result in results.items():
            if not isinstance(result, dict) or set(result) != {'score', 'detected'}:
                raise ValueError(f'Invalid {detector} result: {sid}')
            score = result['score']
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f'Invalid {detector} score: {sid}')
            if type(result['detected']) is not bool:
                raise ValueError(f'Invalid {detector} verdict: {sid}')
    return payload, sorted(rows, key=order_key, reverse=True)


def select_cohort(rows, config):
    owners = defaultdict(list)
    for row in rows:
        if row['owner'] != 'unknown':
            owners[row['owner']].append(row)
    display_ids, qualifying, sample = set(), set(), []
    for owner, members in sorted(owners.items()):
        if len(members) < config['min_tag_size']:
            continue
        qualifying.add(owner)
        eligible = [r for r in sorted(members, key=order_key, reverse=True) if checkable(r, config)]
        newest = eligible[:config['per_tag']]
        sample.extend(newest)
        display_ids.update(row['id'] for row in newest)
    market = [r for r in rows if r['owner'] != 'unknown'
              and r['owner'] != config['market_excluded_owner'] and not r['owner'].startswith('ai:')]
    unknown, eligible_count = [], 0
    for row in rows:
        if row['owner'] != 'unknown':
            continue
        unknown.append(row)
        eligible_count += checkable(row, config)
        if eligible_count >= config['unknown_checkable']:
            break
    targets = {r['id']: r for r in sample + market + unknown}
    evaluated = sorted([r for r in targets.values() if checkable(r, config)], key=lambda r: r['id'])
    evaluated_ids = {r['id'] for r in evaluated}
    for row in rows:
        if row['id'] in evaluated_ids:
            if set(row['results']) != set(DETECTORS):
                raise ValueError(f'Incomplete detector coverage for selected vacancy: {row["id"]}')
        elif row['results']:
            raise ValueError(f'Results outside the evaluated cohort: {row["id"]}')
    if not evaluated:
        raise ValueError('No evaluated vacancies')
    groups = {}
    for owner in sorted(qualifying):
        members = [r['id'] for r in evaluated if r['owner'] == owner and r['id'] in display_ids]
        if members:
            groups[owner] = members
    market_ids = [r['id'] for r in evaluated if r['owner'] != 'unknown'
                  and r['owner'] != config['market_excluded_owner'] and not r['owner'].startswith('ai:')]
    unknown_ids = [r['id'] for r in evaluated if r['owner'] == 'unknown']
    if market_ids:
        groups[AGG_ALL] = market_ids
    if unknown_ids:
        groups[AGG_UNKNOWN] = unknown_ids
    return targets, evaluated, groups


def analyze():
    dataset, rows = load_dataset()
    targets, evaluated, groups = select_cohort(rows, dataset['experiment'])
    by_id = {r['id']: r for r in evaluated}
    summary = {}
    for detector in DETECTORS:
        summary[detector] = {
            'usable_results': len(evaluated),
            'groups': {name: {'n': len(ids),
                              'detected': sum(by_id[sid]['results'][detector]['detected'] for sid in ids),
                              'mean': math.fsum(by_id[sid]['results'][detector]['score'] for sid in ids) / len(ids)}
                       for name, ids in groups.items()}}
    report = {'schema_version': 1, 'dataset_sha256': hashlib.sha256(DATASET.read_bytes()).hexdigest(),
              'pipeline_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'source_rows': len(rows), 'selected_targets': len(targets),
              'checkable_targets': len(evaluated),
              'configuration': dataset['experiment'],
              'selected_ids': sorted(targets), 'evaluated_ids': sorted(by_id),
              'group_members': groups, 'detectors': summary,
              'limitations': dataset['limitations'],
              'python': platform.python_version(),
              'dependencies': {line.split('==')[0]: version(line.split('==')[0])
                               for line in (BASE / 'requirements.txt').read_text().splitlines()
                               if '==' in line}}
    report["comparison_group_members"] = comparison_groups(report, by_id)
    return report, dataset, by_id


def render_charts(report, dataset, by_id, output):
    os.environ.setdefault('MPLCONFIGDIR', str(BASE / '.cache' / 'matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import cm
    from textwrap import fill

    groups = {tag: ids for tag, ids in report['group_members'].items() if len(ids) >= 8}
    stats = report['detectors']
    for detector in DETECTORS:
        title = dataset['detectors'][detector]['title']
        tags = sorted(groups, key=lambda t: (t in AGGREGATES, stats[detector]['groups'][t]['mean'], t))
        means = [stats[detector]['groups'][t]['mean'] for t in tags]
        y = list(range(len(tags) - 1, -1, -1))
        fig, ax = plt.subplots(figsize=(10, max(4, 1.1 * len(tags) + 1.5)))
        ax.barh(y, means, height=0.55, color=[cm.RdYlGn_r(m * 0.85) for m in means],
                edgecolor='black', linewidth=0.6, zorder=2)
        rng = random.Random(42)
        for yi, tag in zip(y, tags):
            big = len(groups[tag]) > 30
            for sid in groups[tag]:
                result = by_id[sid]['results'][detector]
                ax.plot(result['score'], yi + (rng.random() - 0.5) * 0.36, marker='o',
                        markersize=3 if big else 7, alpha=0.5 if big else 1,
                        color='#d62728' if result['detected'] else '#1f3a5f',
                        markeredgecolor='white', markeredgewidth=0.8, zorder=3)
        ax.set_yticks(y)
        ax.set_yticklabels([f"{fill(DISPLAY_NAMES.get(t, t), width=28)}  "
                            f"(n={stats[detector]['groups'][t]['n']}, AI "
                            f"{stats[detector]['groups'][t]['detected']}/{stats[detector]['groups'][t]['n']})"
                            for t in tags])
        ax.set_xlabel('Среднее значение показателя детектора (0–1)')
        ax.set_title(f'ИИ-тексты в вакансиях: компании и агентства ({title})\n'
                     '(точки — отдельные вакансии, красные — распознаны как ИИ)', fontsize=11, pad=14)
        ax.set_xlim(0, 1.02)
        ax.grid(axis='x', linestyle='--', alpha=0.4, zorder=1)
        fig.tight_layout()
        fig.savefig(output / f'{detector}_chart.png', dpi=200)
        plt.close(fig)
    render_comparisons(report, by_id, output)


def comparison_groups(report, by_id):
    """Keep original owner samples and include measured owners below the display threshold."""
    owners = defaultdict(list)
    for row in by_id.values():
        if row['owner'] != 'unknown':
            owners[row['owner']].append(row)
    groups = dict(report['group_members'])
    for owner, rows in owners.items():
        if owner not in groups:
            groups[owner] = [r['id'] for r in sorted(rows, key=order_key, reverse=True)
                             [:report['configuration']['per_tag']]]
    return groups


def render_comparisons(report, by_id, output):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import FixedLocator

    groups = {tag: ids for tag, ids in report['comparison_group_members'].items() if len(ids) >= 8}
    colors = {'aiornot': CHART_THEME['text'], 'pangram': CHART_THEME['strong'],
              'gptzero': CHART_THEME['primary']}
    titles = {'aiornot': 'AI or Not', 'pangram': 'Pangram', 'gptzero': 'GPTZero'}
    names = {'agency:lenkep': 'LENKEP', 'agency:eql': 'EQL / Rivni',
             'agency:apercon': 'Apercon', 'agency:hrise': 'HRise',
             'agency:easy_hunt': 'Easy Hunt', 'agency:hiringband': 'Hiringband',
             'company:affpro': 'Affpro', 'company:devils': 'Devils',
             'company:trident_media': 'Trident Media', 'company:wild_wild_group': 'Wild Wild Group',
             'ai:gemini3.6flash': 'Gemini 3.6 Flash', 'ai:gpt5.6sol-hi': 'GPT 5.6 Sol · Hi',
             'ai:musespark1.3-hi': 'Muse Spark 1.3 · Hi',
             AGG_ALL: 'Agencies + companies\nexcept LENKEP', AGG_UNKNOWN: 'Other'}

    def mean(tag, detector):
        return math.fsum(by_id[sid]['results'][detector]['score'] for sid in groups[tag]) / len(groups[tag])

    def rank(tag):
        return -math.fsum(mean(tag, detector) for detector in DETECTORS), tag

    panels = [
        (COMPARISON_CHARTS[0], 'Agencies', sorted([g for g in groups if g.startswith('agency:')], key=rank)),
        (COMPARISON_CHARTS[1], 'Companies', sorted([g for g in groups if g.startswith('company:')], key=rank)),
        (COMPARISON_CHARTS[2], 'AI & market benchmarks', sorted([g for g in groups if g.startswith('ai:')], key=rank)
         + [g for g in (AGG_ALL, AGG_UNKNOWN) if g in groups]),
    ]
    for filename, title, tags in panels:
        fig = plt.figure(figsize=(10, 10), facecolor=CHART_THEME['background'])
        fig.text(0.025, 0.975, title, fontsize=25, weight='bold', color=CHART_THEME['text'], va='top')
        handles = [Patch(facecolor=colors[d], label=titles[d]) for d in DETECTORS]
        fig.legend(handles=handles, loc='upper left', bbox_to_anchor=(0.015, 0.918),
                   ncol=3, frameon=False, fontsize=11, handlelength=1.2,
                   handleheight=1.0, columnspacing=2.5, labelcolor=CHART_THEME['text'])
        left = 0.24 if filename == COMPARISON_CHARTS[2] else 0.21
        ax = fig.add_axes([left, 0.09, 0.985 - left, 0.77], facecolor=CHART_THEME['background'])
        ax.set_xlim(0, 1.23)
        ax.set_ylim(-0.55, max(len(tags) - 0.45, 0.55))
        ax.xaxis.set_major_locator(FixedLocator([0, 0.25, 0.5, 0.75, 1]))
        ax.set_xticklabels(['0', '0.25', '0.50', '0.75', '1.00'])
        ax.tick_params(axis='x', colors=CHART_THEME['secondary'], labelsize=10, length=0, pad=12)
        ax.set_yticks([])
        ax.set_axisbelow(True)
        ax.grid(axis='x', color=CHART_THEME['border'], linewidth=0.8)
        for spine in ax.spines.values():
            spine.set_visible(False)
        y = list(range(len(tags) - 1, -1, -1))
        for yi, tag in zip(y, tags):
            label = names.get(tag, tag.split(':', 1)[-1].replace('_', ' ').title())
            ax.text(-0.022, yi + 0.11, label, transform=ax.get_yaxis_transform(),
                    ha='right', va='center', fontsize=11, weight='bold', color=CHART_THEME['text'])
            ax.text(-0.022, yi - (0.28 if '\n' in label else 0.18), f"n = {len(groups[tag])}",
                    transform=ax.get_yaxis_transform(), ha='right', va='center',
                    fontsize=9, color=CHART_THEME['secondary'])
            for index, detector in enumerate(DETECTORS):
                yy = yi + (1 - index) * 0.23
                score = mean(tag, detector)
                detected = sum(by_id[sid]['results'][detector]['detected'] for sid in groups[tag])
                ax.barh(yy, score, height=0.19, color=colors[detector], edgecolor='none', zorder=3)
                ax.text(score + 0.025, yy, f"{score:.2f} · {detected}/{len(groups[tag])}",
                        va='center', fontsize=9, color=CHART_THEME['text'], zorder=4)
        ax.set_xlabel('Mean recorded detector score', fontsize=11, color=CHART_THEME['secondary'], labelpad=15)
        fig.savefig(output / filename, dpi=200, facecolor=fig.get_facecolor())
        plt.close(fig)


def run(output=OUTPUT, charts=True):
    report, dataset, by_id = analyze()
    output = Path(output).resolve()
    if output == BASE or output == DATASET.parent or output == DATASET:
        raise ValueError('Output must be separate from source data and code')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Validate and render the whole result before publishing any output files.
    with tempfile.TemporaryDirectory(prefix='.experiment-', dir=output.parent) as tmp:
        staged = Path(tmp)
        if charts:
            render_charts(report, dataset, by_id, staged)
        (staged / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                      sort_keys=True) + '\n', encoding='utf-8')
        output.mkdir(parents=True, exist_ok=True)
        for file in sorted(staged.glob('*.png')):
            os.replace(file, output / file.name)
        (output / 'combined_chart.png').unlink(missing_ok=True)
        if not charts:
            for name in [*(f'{d}_chart.png' for d in DETECTORS), *COMPARISON_CHARTS]:
                (output / name).unlink(missing_ok=True)
        os.replace(staged / 'result.json', output / 'result.json')
    print(f"{report['source_rows']} vacancies; {report['selected_targets']} selected; "
          f"{report['checkable_targets']} evaluated per detector")
    print(f'Result: {output / "result.json"}')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-charts', action='store_true', help='produce the same result JSON without chart images')
    parser.add_argument('--output', type=Path, default=OUTPUT, help='directory for generated result and charts')
    args = parser.parse_args()
    run(args.output, not args.no_charts)


if __name__ == '__main__':
    main()
