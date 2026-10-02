// @vitest-environment node
import en from './locales/en.json';
import ko from './locales/ko.json';
import ja from './locales/ja.json';

type Tree = { [key: string]: string | Tree };

/** 'a.b.c' paths of every string, plural suffixes (_one, _other) dropped. */
function keys(tree: Tree, prefix = ''): Set<string> {
  const out = new Set<string>();
  for (const [k, v] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') out.add(path.replace(/_(one|other)$/, ''));
    else for (const p of keys(v, path)) out.add(p);
  }
  return out;
}

// Sections added with built-in agents, reminders, erase, usage, webhooks
// and eligibility.
const SECTIONS = [
  'agent',
  'projectSettings',
  'integrations',
  'fileCheck.reminder',
  'fileCheck.erase',
  'fileCheck.usage',
  'eligibility',
];

describe('locales', () => {
  it.each([
    ['ko', ko],
    ['ja', ja],
  ])('%s has every new key of en', (_, locale) => {
    const want = [...keys(en as Tree)].filter((k) =>
      SECTIONS.some((s) => k === s || k.startsWith(`${s}.`)),
    );
    const have = keys(locale as Tree);
    expect(want.filter((k) => !have.has(k))).toEqual([]);
  });

  it('keeps {{placeholders}} of each string in ko and ja', () => {
    const vars = (s: string) => (s.match(/\{\{\w+\}\}/g) ?? []).sort();
    const get = (tree: Tree, path: string): string | undefined => {
      let node: string | Tree | undefined = tree;
      for (const part of path.split('.')) {
        node = typeof node === 'object' ? node[part] : undefined;
      }
      return typeof node === 'string' ? node : undefined;
    };
    for (const section of [
      'integrations',
      'fileCheck.reminder',
      'fileCheck.erase',
      'fileCheck.usage',
      'eligibility',
    ]) {
      for (const path of keys(en as Tree)) {
        if (!path.startsWith(`${section}.`)) continue;
        const source = get(en as Tree, path) ?? '';
        for (const locale of [ko, ja]) {
          const text = get(locale as Tree, path) ?? '';
          expect([path, vars(text)]).toEqual([path, vars(source)]);
        }
      }
    }
  });
});
