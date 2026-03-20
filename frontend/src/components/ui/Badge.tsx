// Small coloured badge / pill for status indicators

import clsx from 'clsx'

interface BadgeProps {
  text: string
  color?: string // Tailwind color classes like 'bg-green-100 text-green-800'
  className?: string
}

export default function Badge({ text, color = 'bg-gray-100 text-gray-800', className }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium',
        color,
        className
      )}
    >
      {text}
    </span>
  )
}
