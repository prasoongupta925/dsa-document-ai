import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Info, AlertCircle, X } from 'lucide-react';
import { useModal } from '../hooks/useModal';

export type AlertVariant = 'warning' | 'info' | 'error';

interface AlertModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  message: string;
  buttonText?: string;
  variant?: AlertVariant;
  transparentBackdrop?: boolean;
}

const variantStyles: Record<
  AlertVariant,
  {
    icon: typeof AlertTriangle;
    iconBg: string;
    iconColor: string;
    buttonBg: string;
    borderColor: string;
    glowColor: string;
  }
> = {
  warning: {
    icon: AlertTriangle,
    iconBg: 'bg-amber-100 dark:bg-amber-500/10',
    iconColor: 'text-amber-600 dark:text-amber-400',
    buttonBg: 'bg-amber-600 hover:bg-amber-500',
    borderColor: 'rgba(245, 158, 11, 0.3)',
    glowColor: 'rgba(245, 158, 11, 0.08)',
  },
  info: {
    icon: Info,
    iconBg: 'bg-blue-100 dark:bg-blue-500/10',
    iconColor: 'text-blue-600 dark:text-blue-400',
    buttonBg: 'bg-blue-600 hover:bg-blue-500',
    borderColor: 'rgba(59, 130, 246, 0.3)',
    glowColor: 'rgba(59, 130, 246, 0.08)',
  },
  error: {
    icon: AlertCircle,
    iconBg: 'bg-red-100 dark:bg-red-500/10',
    iconColor: 'text-red-600 dark:text-red-400',
    buttonBg: 'bg-red-600 hover:bg-red-500',
    borderColor: 'rgba(239, 68, 68, 0.3)',
    glowColor: 'rgba(239, 68, 68, 0.08)',
  },
};

export default function AlertModal({
  isOpen,
  onClose,
  title,
  message,
  buttonText,
  variant = 'warning',
  transparentBackdrop = false,
}: AlertModalProps) {
  const { t } = useTranslation();
  const modalRef = useRef<HTMLDivElement>(null);
  const styles = variantStyles[variant];
  const Icon = styles.icon;

  const { handleBackdropClick } = useModal({
    isOpen,
    onClose,
  });

  if (!isOpen) return null;

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200 ${
        transparentBackdrop
          ? ''
          : 'bg-black/55 dark:bg-black/65 backdrop-blur-md'
      }`}
      onClick={handleBackdropClick}
    >
      <div
        ref={modalRef}
        className="confirm-modal-container relative w-full max-w-sm dark:bg-[#1a1d2e] rounded-2xl animate-in zoom-in-95 duration-200 overflow-hidden"
        style={{
          border: `1px solid ${styles.borderColor}`,
          boxShadow: `0 0 40px ${styles.glowColor}, 0 25px 50px -12px rgba(0, 0, 0, 0.15)`,
        }}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-white/10 transition-colors z-10"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="relative p-8">
          <div
            className={`w-14 h-14 mx-auto mb-5 rounded-xl flex items-center justify-center ${styles.iconBg}`}
          >
            <Icon className={`w-7 h-7 ${styles.iconColor}`} />
          </div>

          <h3 className="text-lg font-semibold text-center text-slate-900 dark:text-white mb-2">
            {title}
          </h3>

          <p className="text-sm text-center text-slate-600 dark:text-slate-400 leading-relaxed whitespace-pre-line">
            {message}
          </p>

          <div className="h-8" />

          <button
            onClick={onClose}
            className={`w-full px-5 py-3 text-sm font-medium text-white rounded-xl transition-all ${styles.buttonBg}`}
          >
            {buttonText || t('common.ok', 'OK')}
          </button>
        </div>
      </div>
    </div>
  );
}
