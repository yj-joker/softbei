export function extractUploadedImageUrl(response) {
  if (!response) return ''
  if (typeof response === 'string') return response

  const candidates = [
    response.url,
    response.imageUrl,
    response.fileUrl,
    response.path,
    response.data,
    response.data?.url,
    response.data?.imageUrl,
    response.data?.fileUrl,
    response.data?.path,
  ]

  const found = candidates.find((value) => typeof value === 'string' && value.trim())
  return found ? found.trim() : ''
}
