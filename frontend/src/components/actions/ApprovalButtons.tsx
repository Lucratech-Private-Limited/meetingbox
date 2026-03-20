// Reusable approve / dismiss button pair

import Button from '../ui/Button'

interface ApprovalButtonsProps {
  onApprove: () => void
  onDismiss: () => void
  isApproving?: boolean
  isDismissing?: boolean
}

export default function ApprovalButtons({
  onApprove,
  onDismiss,
  isApproving,
  isDismissing,
}: ApprovalButtonsProps) {
  return (
    <div className="flex items-center justify-between">
      <Button
        variant="secondary"
        onClick={onDismiss}
        isLoading={isDismissing}
      >
        Dismiss
      </Button>
      <Button
        variant="primary"
        onClick={onApprove}
        isLoading={isApproving}
      >
        Approve &amp; Execute
      </Button>
    </div>
  )
}
