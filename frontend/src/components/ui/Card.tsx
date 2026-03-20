// Generic card wrapper with optional header

import { ReactNode } from 'react'
import clsx from 'clsx'

interface CardProps {
  children: ReactNode
  className?: string
}

export default function Card({ children, className }: CardProps) {
  return (
    <div
      className={clsx(
        'bg-white rounded-lg border border-gray-200 overflow-hidden',
        className
      )}
    >
      {children}
    </div>
  )
}
