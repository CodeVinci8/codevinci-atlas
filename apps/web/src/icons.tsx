// Единая оригинальная SVG-иконка навигации (CodeVinci Ember): линейные штрихи,
// один размер/сетка 20×20, currentColor, stroke-based. Не копия сторонних наборов.
// Декоративные (aria-hidden) — подписи дают текстовые метки навигации.

type IconName =
  | "pulse" | "projects" | "profiles" | "runs" | "quality"
  | "autonomy" | "timemachine" | "portfolio";

const PATHS: Record<IconName, React.ReactNode> = {
  // Пульс — линия сердцебиения.
  pulse: <path d="M2 10h4l2-5 3 10 2.5-6H18" />,
  // Проекты — стопка/слои.
  projects: <><path d="M10 2 18 6l-8 4-8-4 8-4Z" /><path d="M2 10l8 4 8-4" /><path d="M2 14l8 4 8-4" /></>,
  // Профили — узлы пула.
  profiles: <><circle cx="6" cy="6" r="2.4" /><circle cx="14" cy="6" r="2.4" /><path d="M2.5 16c0-2.2 1.7-3.6 3.5-3.6S9.5 13.8 9.5 16" /><path d="M10.5 16c0-2.2 1.7-3.6 3.5-3.6s3.5 1.4 3.5 3.6" /></>,
  // Запуски — треугольник play + прогресс.
  runs: <><path d="M6 4.5 14 10l-8 5.5V4.5Z" /></>,
  // Качество — щит с галочкой.
  quality: <><path d="M10 2.5 16.5 5v5c0 3.6-2.7 6.3-6.5 7.5C6.2 16.3 3.5 13.6 3.5 10V5L10 2.5Z" /><path d="M7 10l2 2 4-4.2" /></>,
  // Автономия — узел-гейт (ромб-контроль).
  autonomy: <><path d="M10 2.5 17.5 10 10 17.5 2.5 10 10 2.5Z" /><circle cx="10" cy="10" r="2.4" /></>,
  // Time Machine — циферблат со стрелкой возврата.
  timemachine: <><circle cx="10" cy="10" r="7.2" /><path d="M10 6v4l3 2" /><path d="M3.4 7.5 5.7 8l.5-2.4" /></>,
  // Портфель — сетка карточек.
  portfolio: <><rect x="3" y="3" width="6" height="6" rx="1" /><rect x="11" y="3" width="6" height="6" rx="1" /><rect x="3" y="11" width="6" height="6" rx="1" /><rect x="11" y="11" width="6" height="6" rx="1" /></>,
};

export function NavIcon({ name }: { name: IconName }) {
  return (
    <svg className="nav-svg" width="18" height="18" viewBox="0 0 20 20" fill="none"
      stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

export type { IconName };
