--- Auto-load Groket Neovim commands when this runtimepath is on &rtp.
if vim.g.loaded_groket then
  return
end
vim.g.loaded_groket = true

if vim.fn.has("nvim-0.9") == 0 then
  vim.notify("groket.vim requires Neovim 0.9+", vim.log.levels.ERROR, { title = "groket" })
  return
end

-- Prefer an explicit require("groket").setup({…}) from the user init when present.
-- Only auto-setup when the user has not already configured the client.
if not vim.g.groket_setup_done then
  require("groket").setup()
end
