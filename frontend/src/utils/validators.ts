// Validation utility functions

/** Check if a string is a valid email address */
export function isValidEmail(email: string): boolean {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  return emailRegex.test(email)
}

/** Check if a device name is valid (non-empty, reasonable length) */
export function isValidDeviceName(name: string): boolean {
  const trimmed = name.trim()
  return trimmed.length >= 1 && trimmed.length <= 64
}

/** Check if a string is non-empty after trimming */
export function isNonEmpty(value: string): boolean {
  return value.trim().length > 0
}
