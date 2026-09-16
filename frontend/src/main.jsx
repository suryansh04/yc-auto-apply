import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import { createQueryClient } from './api/hooks.js'
import './styles.css'
import './styles/pipeline.css'
import './styles/review-queue.css'
import './styles/contacts.css'
import './styles/thread.css'

const queryClient = createQueryClient()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
