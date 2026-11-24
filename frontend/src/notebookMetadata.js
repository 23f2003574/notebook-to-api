// The first problem with a tag list or description against the server's own limits (GET /api/config),
// or null. Limits that are unknown are not enforced here: the server still refuses what it must.
export function metadataProblem(tags, description, limits = {}) {
  const { max_tag_length: maxTag, max_tags_per_notebook: maxTags, max_description_length: maxDescription } = limits
  if (maxTags && tags.length > maxTags) return `At most ${maxTags} tags are allowed`
  const tooLong = maxTag && tags.find((tag) => tag.length > maxTag)
  if (tooLong) return `The tag "${tooLong}" is longer than ${maxTag} characters`
  if (maxDescription && description.length > maxDescription) return `The description is longer than ${maxDescription} characters`
  return null
}
