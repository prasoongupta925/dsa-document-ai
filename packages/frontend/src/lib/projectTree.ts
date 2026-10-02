// Projects page, tree view: which loan product a project belongs to (from its
// name, else its description), the customer name to show, search, paging and
// grouping. Pure functions; the component is ../components/ProjectTree.
import type { Project } from '../components/ProjectSettingsModal';
import type { Document } from '../types/project';

/** Group order of the tree; a project naming no known product is 'other'. */
export const PRODUCT_IDS = [
  'personalLoan',
  'homeLoan',
  'loanAgainstProperty',
  'businessLoan',
  'carLoan',
  'educationLoan',
  'balanceTransfer',
  'creditCard',
  'telecallerQa',
  'other',
] as const;

export type ProductId = (typeof PRODUCT_IDS)[number];

// Phrases are case-insensitive; the abbreviations DSA staff write (PL, HL,
// LAP) only match in capitals.
const PRODUCT_PATTERNS: Record<Exclude<ProductId, 'other'>, RegExp[]> = {
  personalLoan: [/\bpersonal[\s-]+loans?\b/i, /\bPL\b/],
  homeLoan: [/\b(?:home|housing)[\s-]+loans?\b/i, /\bHL\b/],
  loanAgainstProperty: [
    /\bloans?\s+against\s+propert(?:y|ies)\b/i,
    /\bmortgage[\s-]+loans?\b/i,
    /\bLAP\b/,
  ],
  businessLoan: [
    /\b(?:business|msme|sme)[\s-]+loans?\b/i,
    /\bworking[\s-]+capital\b/i,
  ],
  carLoan: [/\b(?:car|auto|vehicle|two[\s-]?wheeler)[\s-]+loans?\b/i],
  educationLoan: [/\beducation(?:al)?[\s-]+loans?\b/i],
  balanceTransfer: [/\bbalance[\s-]+transfer\b/i],
  creditCard: [/\bcredit[\s-]+cards?\b/i],
  telecallerQa: [
    /\btele-?call(?:er|ing)s?(?:\s+(?:qa|calls?|reviews?))?\b/i,
    /\bcall[\s-]+(?:qa|quality|reviews?)\b/i,
  ],
};

/** The product named first in the text, or null. */
function productIn(text: string): ProductId | null {
  let best: { id: ProductId; at: number } | null = null;
  for (const [id, patterns] of Object.entries(PRODUCT_PATTERNS)) {
    for (const pattern of patterns) {
      const at = text.search(pattern);
      if (at >= 0 && (best === null || at < best.at)) {
        best = { id: id as ProductId, at };
      }
    }
  }
  return best?.id ?? null;
}

/** The project's loan product: named in its name, else in its description. */
export function detectProduct(
  project: Pick<Project, 'name' | 'description'>,
): ProductId {
  return (
    productIn(project.name) ?? productIn(project.description ?? '') ?? 'other'
  );
}

// "Sneha Kulkarni – Personal Loan", "Home Loan | Ravi", "PL: Ravi".
const NAME_PARTS = /\s+[–—-]\s+|\s*[|·•]\s*|\s*:\s+/;

/** True when the text names the product and nothing else. */
function namesOnly(text: string, product: ProductId): boolean {
  if (product === 'other') return false;
  let rest = text;
  for (const pattern of PRODUCT_PATTERNS[product]) {
    rest = rest.replace(new RegExp(pattern.source, `${pattern.flags}g`), ' ');
  }
  return rest !== text && !/[\p{L}\p{N}]/u.test(rest);
}

/**
 * The customer part of a project name: the name without the part that only
 * names its product ("Sneha Kulkarni – Personal Loan" -> "Sneha Kulkarni").
 * Any other name is kept whole.
 */
export function customerName(name: string, product: ProductId): string {
  const parts = name
    .split(NAME_PARTS)
    .map((part) => part.trim())
    .filter(Boolean);
  const rest = parts.filter((part) => !namesOnly(part, product));
  return rest.length > 0 && rest.length < parts.length
    ? rest.join(' – ')
    : name;
}

export interface TreeProject {
  project: Project;
  product: ProductId;
  /** The name shown in the tree (see customerName). */
  customer: string;
  /** Last updated: updated_at, else created_at ('' if neither). */
  updated: string;
}

const time = (iso: string) => {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? 0 : ms;
};

/** Tree rows in group order, the most recently updated first in each group. */
export function toTreeProjects(projects: Project[]): TreeProject[] {
  const order = new Map<ProductId, number>(
    PRODUCT_IDS.map((id, index) => [id, index]),
  );
  return projects
    .map((project) => {
      const product = detectProduct(project);
      return {
        project,
        product,
        customer: customerName(project.name, product),
        updated: project.updated_at || project.created_at || '',
      };
    })
    .sort(
      (a, b) =>
        (order.get(a.product) ?? 0) - (order.get(b.product) ?? 0) ||
        time(b.updated) - time(a.updated) ||
        a.customer.localeCompare(b.customer),
    );
}

/**
 * Rows where every word of the query is found in the name, the product or
 * the case handler (case-insensitive).
 */
export function filterTreeProjects(
  rows: TreeProject[],
  query: string,
  productLabel: (product: ProductId) => string,
): TreeProject[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return rows;
  return rows.filter((row) => {
    const haystack = [
      row.project.name,
      row.customer,
      productLabel(row.product),
      row.project.created_by ?? '',
    ]
      .join('\n')
      .toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

export interface TreeGroup {
  product: ProductId;
  rows: TreeProject[];
}

/** The rows by product, in group order; rows keep their order. */
export function groupTreeProjects(rows: TreeProject[]): TreeGroup[] {
  const byProduct = new Map<ProductId, TreeProject[]>();
  for (const row of rows) {
    const list = byProduct.get(row.product);
    if (list) list.push(row);
    else byProduct.set(row.product, [row]);
  }
  return PRODUCT_IDS.flatMap((product) => {
    const list = byProduct.get(product);
    return list ? [{ product, rows: list }] : [];
  });
}

/** How many rows each product has. */
export function countByProduct(rows: TreeProject[]): Map<ProductId, number> {
  const counts = new Map<ProductId, number>();
  for (const row of rows) {
    counts.set(row.product, (counts.get(row.product) ?? 0) + 1);
  }
  return counts;
}

// ------------------------------------------------------------------- paging

export const PAGE_SIZES = [10, 25, 50] as const;
export type PageSize = (typeof PAGE_SIZES)[number];

/** Number of pages (at least 1, so an empty table still has page 1). */
export function pageCount(total: number, size: number): number {
  return Math.max(1, Math.ceil(total / size));
}

/**
 * Page buttons to show: every page up to 7 pages, else the first, the last
 * and the pages around the current one, with null for a gap.
 */
export function pageWindow(current: number, count: number): (number | null)[] {
  if (count <= 7) return Array.from({ length: count }, (_, i) => i + 1);
  const start = Math.max(2, Math.min(current - 1, count - 4));
  const end = Math.min(count - 1, Math.max(current + 1, 5));
  const pages: (number | null)[] = [1];
  if (start > 2) pages.push(null);
  for (let page = start; page <= end; page++) pages.push(page);
  if (end < count - 1) pages.push(null);
  pages.push(count);
  return pages;
}

// ---------------------------------------------------------------- documents

/** Number of documents, and of those completed. */
export function documentProgress(documents: Pick<Document, 'status'>[]): {
  total: number;
  completed: number;
} {
  return {
    total: documents.length,
    completed: documents.filter((d) => d.status === 'completed').length,
  };
}

// ------------------------------------------------------- Tree | Cards switch

export type ProjectsView = 'tree' | 'cards';

export const PROJECTS_VIEW_KEY = 'idp-projects-view';

/** The view chosen last in this browser; the tree when none (or no storage). */
export function readProjectsView(): ProjectsView {
  try {
    return localStorage.getItem(PROJECTS_VIEW_KEY) === 'cards'
      ? 'cards'
      : 'tree';
  } catch {
    return 'tree';
  }
}

/** Remembers the view; without storage it lasts until the page reloads. */
export function writeProjectsView(view: ProjectsView): void {
  try {
    localStorage.setItem(PROJECTS_VIEW_KEY, view);
  } catch {
    // Blocked or full storage: nothing else to do.
  }
}
