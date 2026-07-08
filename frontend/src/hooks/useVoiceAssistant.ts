import { useCallback, useEffect, useRef, useState } from 'react'

type VoiceState = 'idle' | 'listening' | 'processing'

export interface UseVoiceAssistantResult {
  state: VoiceState
  isListening: boolean
  transcript: string
  startListening: () => void
  stopListening: () => void
  reset: () => void
}

// Use `any` for the recognition instance — Web Speech API types are not
// guaranteed to be present in every TS lib configuration.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRecognition = any

function getSpeechRecognitionCtor(): AnyRecognition | null {
  if (typeof window === 'undefined') return null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function useVoiceAssistant(): UseVoiceAssistantResult {
  const [state, setState] = useState<VoiceState>('idle')
  const [transcript, setTranscript] = useState('')
  const recognitionRef = useRef<AnyRecognition>(null)

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop()
    setState('idle')
  }, [])

  const startListening = useCallback(() => {
    const SR = getSpeechRecognitionCtor()
    if (!SR) return

    const recognition = new SR()
    recognition.lang = 'en-US'
    recognition.interimResults = false
    recognition.maxAlternatives = 1

    recognition.onstart = () => setState('listening')

    recognition.onresult = (event: { results: { [key: number]: { [key: number]: { transcript: string } } } }) => {
      const text: string = event.results[0]?.[0]?.transcript ?? ''
      setTranscript(text)
      setState('processing')
    }

    recognition.onend = () => {
      setState((prev: VoiceState) => (prev === 'listening' ? 'idle' : prev))
    }

    recognition.onerror = () => {
      setState('idle')
    }

    recognitionRef.current = recognition
    setTranscript('')
    recognition.start()
  }, [])

  const reset = useCallback(() => {
    recognitionRef.current?.stop()
    setTranscript('')
    setState('idle')
  }, [])

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop()
    }
  }, [])

  return {
    state,
    isListening: state === 'listening',
    transcript,
    startListening,
    stopListening,
    reset,
  }
}
