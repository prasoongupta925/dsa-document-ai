import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeft,
  Calculator,
  FlaskConical,
  Info,
  Landmark,
  Loader2,
  RefreshCw,
  Save,
  X,
} from 'lucide-react';
import type {
  EligibilityInputs,
  EligibilityResult,
  LenderEligibility,
  LenderPolicy,
} from '../../types/eligibility';
import {
  newDraft,
  type EligibilityDraft,
  type EligibilityState,
} from '../../hooks/useEligibility';
import {
  countInvalidNumbers,
  inputsKey,
  lenderTone,
  stillNeeded,
} from '../../lib/eligibility';
import { maskPan } from '../../lib/fileCheck';
import ProfileSection from './ProfileSection';
import CibilSection from './CibilSection';
import LendersSection from './LendersSection';
import EmiCalculator, { emiStartOf } from './EmiCalculator';
import BranchFinder from './BranchFinder';
import { describeEligibilityError } from './errors';
import { BUTTON_CLASS, PRIMARY_CLASS } from './fields';
import { companyCheckRows, pincodeCheckRows, type PolicyCheck } from './checks';

export type EligibilityTab = 'profile' | 'cibil' | 'lenders';

const TABS: EligibilityTab[] = ['profile', 'cibil', 'lenders'];

interface EligibilityPanelProps {
  state: EligibilityState;
  /** The project: the Lenders tab shows the nearest branches with it. */
  projectId?: string;
  /**
   * What the API calls the applicant: the verdict's PAN when it has one,
   * else the name (the same value finds the saved inputs again).
   */
  applicant: string;
  /** The applicant's name as the file check shows it. */
  name: string;
  /** The verdict's PAN; only its last 4 characters are shown. */
  pan?: string | null;
  /** Back to the File Check panel. */
  onBack: () => void;
  onClose: () => void;
  /** Tab shown first (tests render one tab statically). */
  initialTab?: EligibilityTab;
}

/**
 * The lenders whose nearest branches the Lenders tab shows: the eligible ones
 * of the result, the best first; before a calculation, every policy's lender.
 */
export function branchLendersOf(
  result: EligibilityResult | null,
  policies: LenderPolicy[] | undefined,
): string[] {
  if (!result) return (policies ?? []).map((lender) => lender.name);
  const eligible = result.per_lender
    .filter((row) => lenderTone(row.status) === 'eligible')
    .map((row) => row.lender);
  const best = result.best_lender;
  return best && eligible.includes(best)
    ? [best, ...eligible.filter((name) => name !== best)]
    : eligible;
}

/**
 * Eligibility & lenders for one applicant: the client's three sheets as three
 * tabs (Profile, CIBIL, Lenders). Overlays the side panel like File Check.
 */
export default function EligibilityPanel({
  state,
  projectId,
  applicant,
  name,
  pan,
  onBack,
  onClose,
  initialTab = 'profile',
}: EligibilityPanelProps) {
  const { t } = useTranslation();
  const baseId = useId();
  const [tab, setTab] = useState<EligibilityTab>(initialTab);
  const tabRefs = useRef<Record<EligibilityTab, HTMLButtonElement | null>>({
    profile: null,
    cibil: null,
    lenders: null,
  });
  const [pincodeCheck, setPincodeCheck] = useState<PolicyCheck | null>(null);
  const [companyCheck, setCompanyCheck] = useState<PolicyCheck | null>(null);
  const checkSeq = useRef({ pincode: 0, company: 0 });
  const {
    drafts,
    load,
    edit,
    save,
    calculate,
    login,
    checkPincode,
    checkCompany,
    lenders,
    lendersLoading,
    lendersError,
    loadLenders,
  } = state;
  const draft: EligibilityDraft = drafts[applicant] ?? newDraft(applicant);
  const { inputs } = draft;

  useEffect(() => {
    load(applicant);
  }, [applicant, load]);

  useEffect(() => {
    if (!lenders && !lendersLoading && lendersError == null) loadLenders();
  }, [lenders, lendersLoading, lendersError, loadLenders]);

  // Another applicant: first tab, no check results (late answers ignored).
  const [shownApplicant, setShownApplicant] = useState(applicant);
  if (shownApplicant !== applicant) {
    setShownApplicant(applicant);
    setTab(initialTab);
    setPincodeCheck(null);
    setCompanyCheck(null);
    checkSeq.current.pincode += 1;
    checkSeq.current.company += 1;
  }

  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented) onBack();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onBack]);

  const key = useMemo(() => inputsKey(inputs), [inputs]);
  const dirty = draft.loaded && key !== draft.savedKey;
  const stale = !!draft.result && key !== draft.resultKey;
  const busy = !draft.loaded || draft.loading;
  // Typed text that is not a number would be sent as blank: fix it first.
  const invalidNumbers = useMemo(() => countInvalidNumbers(inputs), [inputs]);
  const branchLenders = useMemo(
    () => branchLendersOf(draft.result, lenders?.lenders),
    [draft.result, lenders],
  );

  const onEdit = useCallback(
    (update: (i: EligibilityInputs) => EligibilityInputs) =>
      edit(applicant, update),
    [edit, applicant],
  );

  const handleSave = () => {
    if (!draft.saving && invalidNumbers === 0) save(applicant, inputs);
  };

  const handleCalculate = async () => {
    if (draft.calculating || invalidNumbers > 0) return;
    const result = await calculate(applicant, inputs);
    if (result) setTab('lenders');
  };

  // A login is recomputed from the SAVED inputs: save what is on screen first.
  const handleLogin = (row: LenderEligibility) => {
    login(
      applicant,
      { id: row.lender_id, name: row.lender },
      dirty || !draft.saved ? inputs : undefined,
    );
  };

  const handleCheckPincode = async () => {
    const pincode = (inputs.profile.pincode ?? '').trim();
    if (!pincode) return;
    const n = ++checkSeq.current.pincode;
    setPincodeCheck({ value: pincode, rows: [], loading: true });
    try {
      const check = await checkPincode(pincode);
      if (n === checkSeq.current.pincode) {
        setPincodeCheck(pincodeCheckRows(t, check));
      }
    } catch (err) {
      if (n === checkSeq.current.pincode) {
        setPincodeCheck({
          value: pincode,
          rows: [],
          error: describeEligibilityError(t, err),
        });
      }
    }
  };

  const handleCheckCompany = async (picked?: string) => {
    const company = (picked ?? inputs.profile.company ?? '').trim();
    if (!company) return;
    const n = ++checkSeq.current.company;
    setCompanyCheck({ value: company, rows: [], loading: true });
    try {
      const check = await checkCompany(company);
      if (n === checkSeq.current.company) {
        setCompanyCheck(companyCheckRows(t, check));
      }
    } catch (err) {
      if (n === checkSeq.current.company) {
        setCompanyCheck({
          value: company,
          rows: [],
          error: describeEligibilityError(t, err),
        });
      }
    }
  };

  const selectTab = (next: EligibilityTab, focus = false) => {
    setTab(next);
    if (focus) tabRefs.current[next]?.focus();
  };

  const onTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const i = TABS.indexOf(tab);
    let next: EligibilityTab | null = null;
    if (e.key === 'ArrowRight') next = TABS[(i + 1) % TABS.length];
    else if (e.key === 'ArrowLeft') {
      next = TABS[(i - 1 + TABS.length) % TABS.length];
    } else if (e.key === 'Home') next = TABS[0];
    else if (e.key === 'End') next = TABS[TABS.length - 1];
    if (next) {
      e.preventDefault();
      selectTab(next, true);
    }
  };

  const maskedPan = maskPan(pan);
  const savedTime = draft.savedAt?.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
  const expiresOn = draft.expiresAt
    ? new Date(draft.expiresAt).toLocaleDateString([], {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    : null;
  const saveStatus = draft.saving
    ? t('eligibility.footer.saving')
    : dirty
      ? t('eligibility.footer.unsaved')
      : draft.savedAt
        ? t('eligibility.footer.savedAt', { time: savedTime })
        : draft.saved
          ? t('eligibility.footer.saved')
          : t('eligibility.footer.notSaved');
  const draftInfo = [
    !draft.saved && draft.prefill?.detail ? draft.prefill.detail : null,
    ...draft.notes,
  ].filter((line): line is string => !!line);

  return (
    <div
      className="artifact-viewer-container absolute inset-0 z-10 flex flex-col overflow-hidden rounded-xl border border-white/60 animate-fade-in dark:border-indigo-500/20"
      role="region"
      aria-label={t('eligibility.title')}
      data-testid="eligibility-panel"
    >
      {/* Header */}
      <div className="flex flex-shrink-0 items-center gap-2 border-b border-black/[0.08] bg-transparent px-3 py-3 dark:border-white/[0.08] dark:bg-white/[0.05]">
        <button
          type="button"
          onClick={onBack}
          className="flex flex-shrink-0 items-center gap-1 rounded-lg px-1.5 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:text-slate-300 dark:hover:bg-white/10 dark:hover:text-white"
          aria-label={t('eligibility.backLabel')}
          title={t('eligibility.backLabel')}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          <span className="hidden sm:inline">{t('eligibility.back')}</span>
        </button>
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600">
          <Landmark className="h-4 w-4 text-white" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-slate-800 dark:text-slate-200">
            {t('eligibility.title')}
          </h3>
          <p className="truncate text-xs text-slate-500 dark:text-slate-400">
            {name}
            {maskedPan &&
              ` · ${t('eligibility.subtitlePan', { pan: maskedPan })}`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            // Reloading discards what is not saved: ask first.
            if (dirty && !window.confirm(t('eligibility.reloadConfirm'))) {
              return;
            }
            load(applicant, true);
          }}
          disabled={draft.loading || draft.saving}
          className="flex-shrink-0 rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:opacity-40 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-slate-200"
          aria-label={t('eligibility.reload')}
          title={t('eligibility.reload')}
        >
          <RefreshCw
            className={`h-4 w-4 ${draft.loading ? 'animate-spin' : ''}`}
            aria-hidden="true"
          />
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex-shrink-0 rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-slate-200"
          aria-label={t('common.close', 'Close')}
          title={t('common.close', 'Close')}
        >
          <X className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>

      {/* Every policy value here is SAMPLE data; results are indicative */}
      <p
        className="flex flex-shrink-0 items-start gap-1.5 border-b border-amber-200/70 bg-amber-50/80 px-4 py-1.5 text-[10px] leading-snug text-amber-900 dark:border-amber-800/40 dark:bg-amber-900/20 dark:text-amber-200"
        data-testid="sample-banner"
      >
        <FlaskConical
          className="mt-px h-3 w-3 flex-shrink-0"
          aria-hidden="true"
        />
        <span>
          <strong className="font-semibold">
            {t('eligibility.samplePolicy')}.
          </strong>{' '}
          {t('eligibility.banner')}
        </span>
      </p>

      {/* The client's three sheets */}
      <div
        role="tablist"
        aria-label={t('eligibility.tabs.label')}
        className="flex flex-shrink-0 gap-1 border-b border-black/[0.08] px-3 pt-2 dark:border-white/[0.08]"
      >
        {TABS.map((id, i) => {
          const selected = tab === id;
          return (
            <button
              key={id}
              ref={(el) => {
                tabRefs.current[id] = el;
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => selectTab(id)}
              onKeyDown={onTabKeyDown}
              className={`-mb-px flex items-center gap-1.5 rounded-t-lg border-b-2 px-2.5 py-1.5 text-xs font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500 ${
                selected
                  ? 'border-indigo-600 text-indigo-700 dark:border-indigo-400 dark:text-indigo-300'
                  : 'border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
              }`}
            >
              <span
                aria-hidden="true"
                className={`flex h-4 w-4 items-center justify-center rounded-full text-[9px] ${
                  selected
                    ? 'bg-indigo-600 text-white dark:bg-indigo-500'
                    : 'bg-slate-200 text-slate-600 dark:bg-white/10 dark:text-slate-300'
                }`}
              >
                {i + 1}
              </span>
              {t(`eligibility.tabs.${id}`)}
              {id === 'lenders' && stale && (
                <span
                  className="h-1.5 w-1.5 rounded-full bg-amber-500"
                  aria-hidden="true"
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div
        id={`${baseId}-panel`}
        role="tabpanel"
        aria-labelledby={`${baseId}-tab-${tab}`}
        tabIndex={0}
        className="@container min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3 focus:outline-none"
      >
        {draft.loadError != null && !draft.loading ? (
          <div
            role="alert"
            className="space-y-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800/50 dark:bg-red-900/20 dark:text-red-300"
          >
            <p className="break-words">
              {t('eligibility.loadFailed', {
                message: describeEligibilityError(t, draft.loadError),
              })}
            </p>
            <button
              type="button"
              onClick={() => load(applicant, true)}
              className={BUTTON_CLASS}
            >
              <RefreshCw className="h-3 w-3" aria-hidden="true" />
              {t('eligibility.retry')}
            </button>
          </div>
        ) : busy ? (
          <div className="flex flex-col items-center justify-center gap-2 py-10 text-xs text-slate-500">
            <Loader2
              className="h-6 w-6 animate-spin text-indigo-500"
              aria-hidden="true"
            />
            {t('eligibility.loading')}
          </div>
        ) : (
          <>
            {tab !== 'lenders' && draftInfo.length > 0 && (
              <ul
                className="space-y-0.5 rounded-lg border border-blue-200/70 bg-blue-50/60 px-2.5 py-1.5 text-[10px] leading-snug text-blue-900 dark:border-blue-800/40 dark:bg-blue-900/15 dark:text-blue-200"
                data-testid="draft-notes"
              >
                {draftInfo.map((line, i) => (
                  <li key={i} className="flex items-start gap-1">
                    <Info
                      className="mt-px h-3 w-3 flex-shrink-0"
                      aria-hidden="true"
                    />
                    <span className="min-w-0 break-words">{line}</span>
                  </li>
                ))}
              </ul>
            )}
            {tab === 'profile' ? (
              <ProfileSection
                inputs={inputs}
                onEdit={onEdit}
                prefill={draft.prefillValues}
                incomeSource={draft.prefill?.income_source}
                pincodeCheck={pincodeCheck}
                companyCheck={companyCheck}
                onCheckPincode={handleCheckPincode}
                onCheckCompany={handleCheckCompany}
              />
            ) : tab === 'cibil' ? (
              <CibilSection cibil={inputs.cibil} onEdit={onEdit} />
            ) : (
              <LendersSection
                applicantName={name}
                result={draft.result}
                stale={stale}
                calculating={draft.calculating}
                calcError={draft.calcError}
                calculatedAt={draft.calculatedAt}
                onCalculate={handleCalculate}
                canCalculate={invalidNumbers === 0}
                lenders={lenders?.lenders ?? []}
                logins={draft.logins}
                onLogin={handleLogin}
                stillNeeded={stillNeeded(inputs, draft.prefillValues.fields)}
              />
            )}
            {tab === 'lenders' && projectId && (draft.result || lenders) && (
              <BranchFinder
                projectId={projectId}
                pincode={inputs.profile.pincode ?? undefined}
                lenders={branchLenders}
              />
            )}
            {tab === 'lenders' && (
              <EmiCalculator start={emiStartOf(draft.result)} />
            )}
          </>
        )}
      </div>

      {/* Save and calculate */}
      <div className="flex-shrink-0 space-y-1.5 border-t border-black/[0.08] px-4 py-2 dark:border-white/[0.08]">
        {invalidNumbers > 0 && (
          <p
            role="alert"
            className="text-[11px] text-red-600 dark:text-red-400"
            data-testid="invalid-numbers"
          >
            {t('eligibility.footer.fixNumbers', { count: invalidNumbers })}
          </p>
        )}
        {draft.saveError != null && (
          <p
            role="alert"
            className="break-words text-[11px] text-red-600 dark:text-red-400"
          >
            {t('eligibility.footer.saveFailed', {
              message: describeEligibilityError(t, draft.saveError),
            })}
          </p>
        )}
        <div className="flex items-center gap-2">
          <p
            className={`min-w-0 flex-1 truncate text-[11px] ${
              dirty
                ? 'font-medium text-amber-700 dark:text-amber-400'
                : 'text-slate-500 dark:text-slate-400'
            }`}
            role="status"
            data-testid="save-status"
          >
            {saveStatus}
          </p>
          <button
            type="button"
            onClick={handleSave}
            disabled={
              busy ||
              draft.saving ||
              invalidNumbers > 0 ||
              (!dirty && draft.saved)
            }
            className={BUTTON_CLASS}
          >
            {draft.saving ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-3 w-3" aria-hidden="true" />
            )}
            {t('eligibility.footer.save')}
          </button>
          <button
            type="button"
            onClick={handleCalculate}
            disabled={busy || draft.calculating || invalidNumbers > 0}
            className={PRIMARY_CLASS}
          >
            {draft.calculating ? (
              <Loader2
                className="h-3.5 w-3.5 animate-spin"
                aria-hidden="true"
              />
            ) : (
              <Calculator className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {draft.calculating
              ? t('eligibility.lenders.calculating')
              : t('eligibility.lenders.calculate')}
          </button>
        </div>
        <p className="text-[10px] leading-snug text-slate-500 dark:text-slate-400">
          {expiresOn
            ? t('eligibility.footer.retentionOn', { date: expiresOn })
            : t('eligibility.footer.retention')}{' '}
          {t('eligibility.syntheticNote')}
        </p>
      </div>
    </div>
  );
}
