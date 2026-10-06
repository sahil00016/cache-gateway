import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Each hook sets its own refetchInterval; a stale time of 0 keeps the
      // displayed value and the polled value the same thing.
      staleTime: 0,
      retry: 1,
      // The dashboard is a live view. Refetching on focus would make the
      // sparkline jump whenever the operator alt-tabs back.
      refetchOnWindowFocus: false,
    },
  },
})

const root = document.getElementById('root')
if (!root) throw new Error('#root is missing from index.html')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
