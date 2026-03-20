import { useEffect, useRef, useState, useCallback } from 'react'

const MIN_RECONNECT_DELAY = 1000
const MAX_RECONNECT_DELAY = 30000

export function useWebSocket() {
  const [lastMessage, setLastMessage] = useState<MessageEvent | null>(null)
  const [readyState, setReadyState] = useState<number>(WebSocket.CONNECTING)
  const ws = useRef<WebSocket | null>(null)
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const backoff = useRef(MIN_RECONNECT_DELAY)
  const unmounted = useRef(false)

  useEffect(() => {
    unmounted.current = false

    const connect = () => {
      if (unmounted.current) return

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/ws`
      ws.current = new WebSocket(wsUrl)

      ws.current.onopen = () => {
        if (unmounted.current) return
        setReadyState(WebSocket.OPEN)
        backoff.current = MIN_RECONNECT_DELAY
      }

      ws.current.onmessage = (event) => {
        if (unmounted.current) return
        setLastMessage(event)
      }

      ws.current.onerror = () => {
        // onclose will fire after this
      }

      ws.current.onclose = () => {
        if (unmounted.current) return
        setReadyState(WebSocket.CLOSED)

        reconnectTimeout.current = setTimeout(() => {
          backoff.current = Math.min(backoff.current * 2, MAX_RECONNECT_DELAY)
          connect()
        }, backoff.current)
      }
    }

    connect()

    return () => {
      unmounted.current = true
      if (reconnectTimeout.current) {
        clearTimeout(reconnectTimeout.current)
        reconnectTimeout.current = null
      }
      if (ws.current) {
        ws.current.onopen = null
        ws.current.onmessage = null
        ws.current.onerror = null
        ws.current.onclose = null
        ws.current.close()
        ws.current = null
      }
    }
  }, [])

  const sendMessage = useCallback((message: string) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(message)
    }
  }, [])

  return { lastMessage, sendMessage, readyState }
}
