# Reset all ChromaDB collections (pod_catalog, meme_repo, brand_guidelines)
# and re-seed from scratch. User memories are also deleted.

$chromaDir = "backend/chroma_db"

if (Test-Path $chromaDir) {
    Write-Host "Deleting $chromaDir ..."
    Remove-Item -Recurse -Force $chromaDir
}

Write-Host "Re-seeding collections..."
python -m backend.rag.loader

Write-Host "Done."
