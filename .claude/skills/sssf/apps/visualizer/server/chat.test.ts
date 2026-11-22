import { describe, expect, test } from 'bun:test'
import { allowedRepos, repoFor, validateMessage, validWorkflow } from './chat'

const root = process.cwd()

describe('chat repository boundary', () => {
  test('only exposes existing absolute allowlisted repositories', () => {
    const repos = allowedRepos(root, `${root},/definitely/not-a-repo`)
    expect(repos).toHaveLength(1)
    expect(repoFor(repos, repos[0]?.id)?.path).toBe(root)
  })
  test('rejects workflow injection values and blank prompts', () => {
    expect(validWorkflow('scout')).toBe(true)
    expect(validWorkflow('just; rm -rf /')).toBe(false)
    expect(() => validateMessage('   ')).toThrow()
  })
})
