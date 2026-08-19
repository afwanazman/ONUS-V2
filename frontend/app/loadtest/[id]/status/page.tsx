'use client'

import { use } from 'react'
import { LoadTestResults } from '@/components/loadtest-results'

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  return <LoadTestResults jobId={id} />
}
