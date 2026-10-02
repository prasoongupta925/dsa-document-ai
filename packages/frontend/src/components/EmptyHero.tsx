import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';

const fadeUp = {
  hidden: { opacity: 0, y: 30 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.8,
      delay: 0.2 + i * 0.15,
      ease: [0.25, 0.4, 0.25, 1] as const,
    },
  }),
};

export default function EmptyHero({
  onCreateProject,
}: {
  onCreateProject: () => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="empty-hero">
      <motion.h1
        custom={0}
        variants={fadeUp}
        initial="hidden"
        animate="visible"
        className="empty-hero-title"
      >
        <span className="empty-hero-title-main">
          {t('projects.emptyWelcome')}
        </span>
        <br />
        <span className="empty-hero-title-accent">
          {t('projects.createFirst')}
        </span>
      </motion.h1>

      <motion.button
        custom={1}
        variants={fadeUp}
        initial="hidden"
        animate="visible"
        className="empty-hero-cta"
        onClick={onCreateProject}
      >
        <svg
          className="w-5 h-5"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 4v16m8-8H4"
          />
        </svg>
        {t('projects.newProject')}
      </motion.button>
    </div>
  );
}
