--- Live Groket sessions over the control Unix socket (Neovim 0.9+).
--- Requires a live control owner: ``groket serve start -d`` or a TUI/auto-serve.

local M = {}

local uv = vim.uv or vim.loop

---@class groket.Config
---@field socket string|nil absolute path, or nil for runtime default
---@field executable string groket CLI for optional TUI start
---@field timeout_ms integer request wait
---@field auto_start boolean start TUI when socket missing and session is a directory
---@field keys table<string, string|false>|nil global maps; set false to disable
---@field picker "auto"|"select"|"telescope"|"fzf-lua"|"mini"|"snacks"|nil
---Session buffer highlighting for fenced ```markdown transcript bodies.
---@field highlight "soft"|"full"|"calm"|"none"|nil

M.config = {
  socket = nil,
  executable = "groket",
  timeout_ms = 10000,
  -- Detached Textual without a PTY is unreliable; prefer an already-running TUI.
  auto_start = false,
  -- Markdown projection only (HTML comment anchors). Org remains the Emacs client.
  format = "markdown",
  -- soft/full: treesitter + no code-block grey bg. calm: classic syntax only.
  -- none: plain text. Cycle with <LocalLeader>h / :GroketHighlight.
  highlight = "soft",
  -- Reload projection when notes/trace change remotely (skipped if buffer modified).
  auto_refresh = true,
  -- auto: telescope/fzf-lua/mini/snacks when installed, else vim.ui.select
  picker = "auto",
  keys = {
    find = "<leader>gs", -- pick session (ui.select / ecosystem picker)
    list = "<leader>gl", -- session browser buffer
    refresh = "<leader>gR", -- refresh open projection
    connect = "<leader>gc",
  },
}

local state = {
  sock = nil, ---@type uv.uv_pipe_t|nil
  read_buf = "",
  next_id = 1,
  pending = {}, ---@type table<integer, {resolve: fun(any), reject: fun(string)}>
  started_job = nil, ---@type integer|nil
}

local function default_socket_path()
  if M.config.socket and M.config.socket ~= "" then
    return vim.fn.expand(M.config.socket)
  end
  local runtime = vim.env.XDG_RUNTIME_DIR
  if runtime and runtime ~= "" then
    return runtime .. "/groket/control.sock"
  end
  return vim.fn.expand("~/.groket/run/control.sock")
end

local function notify(msg, level)
  vim.schedule(function()
    vim.notify(msg, level or vim.log.levels.INFO, { title = "groket" })
  end)
end

local function decode_line(line)
  local ok, obj = pcall(vim.json.decode, line)
  if ok then
    return obj
  end
  return nil
end

local function resume_waiter(id, fn)
  local waiter = state.pending[id]
  if not waiter then
    return
  end
  state.pending[id] = nil
  vim.schedule(function()
    fn(waiter)
  end)
end

local function handle_message(msg)
  if msg.id ~= nil then
    resume_waiter(msg.id, function(waiter)
      if msg.error then
        local detail = msg.error.message or "request failed"
        local data = msg.error.data
        if type(data) == "table" and data.currentRevision then
          detail = detail .. " (revision " .. tostring(data.currentRevision) .. ")"
        end
        waiter.reject(detail)
      else
        waiter.resolve(msg.result)
      end
    end)
    return
  end
  if msg.method then
    vim.schedule(function()
      M._on_notification(msg.method, msg.params or {})
    end)
  end
end

local function on_read(err, data)
  if err then
    notify("control read error: " .. tostring(err), vim.log.levels.ERROR)
    vim.schedule(function()
      M.disconnect()
    end)
    return
  end
  if data == nil then
    -- EOF: a final frame without its newline still carries a reply; deliver it
    -- so the waiter settles instead of burning the request timeout.
    local tail = state.read_buf
    state.read_buf = ""
    if tail:match("%S") then
      local msg = decode_line(tail)
      if msg then
        handle_message(msg)
      end
    end
    vim.schedule(function()
      M.disconnect()
    end)
    return
  end
  state.read_buf = state.read_buf .. data
  while true do
    local nl = state.read_buf:find("\n", 1, true)
    if not nl then
      break
    end
    local line = state.read_buf:sub(1, nl - 1)
    state.read_buf = state.read_buf:sub(nl + 1)
    if line:match("%S") then
      local msg = decode_line(line)
      if msg then
        handle_message(msg)
      end
    end
  end
end

function M.disconnect()
  for id, waiter in pairs(state.pending) do
    state.pending[id] = nil
    vim.schedule(function()
      waiter.reject("disconnected")
    end)
  end
  if state.sock then
    pcall(function()
      state.sock:read_stop()
      state.sock:close()
    end)
    state.sock = nil
  end
  state.read_buf = ""
end

function M.connected()
  return state.sock ~= nil and not state.sock:is_closing()
end

---@param method string
---@param params table|nil
---@return any
function M.request(method, params)
  if not M.connected() then
    error("not connected to groket control socket")
  end
  local co = coroutine.running()
  if not co then
    error("groket.request must run inside a coroutine")
  end
  local id = state.next_id
  state.next_id = id + 1
  local payload = vim.json.encode({
    jsonrpc = "2.0",
    id = id,
    method = method,
    params = params or vim.empty_dict(),
  })
  local result, err_msg
  local settled = false
  state.pending[id] = {
    resolve = function(value)
      if settled then
        return
      end
      settled = true
      result = value
      coroutine.resume(co)
    end,
    reject = function(message)
      if settled then
        return
      end
      settled = true
      err_msg = message
      coroutine.resume(co)
    end,
  }
  state.sock:write(payload .. "\n", function(write_err)
    if write_err then
      resume_waiter(id, function(waiter)
        waiter.reject("write failed: " .. tostring(write_err))
      end)
    end
  end)
  vim.defer_fn(function()
    if state.pending[id] then
      resume_waiter(id, function(waiter)
        waiter.reject("timeout waiting for " .. method)
      end)
    end
  end, M.config.timeout_ms)
  coroutine.yield()
  if err_msg then
    if tostring(err_msg):find("method not found", 1, true) then
      error(
        err_msg
          .. " — restart the groket TUI from a build that includes this control method ("
          .. method
          .. ")"
      )
    end
    error(err_msg)
  end
  return result
end

local function wait_for_socket(timeout_ms)
  local deadline = uv.now() + (timeout_ms or M.config.timeout_ms)
  while uv.now() < deadline do
    if uv.fs_stat(default_socket_path()) then
      return true
    end
    vim.wait(50)
  end
  return uv.fs_stat(default_socket_path()) ~= nil
end

function M.start_tui(session)
  local sock = default_socket_path()
  local args = { M.config.executable, "--control-socket", sock }
  if session and session ~= "" then
    args = {
      M.config.executable,
      "--path",
      vim.fn.fnamemodify(session, ":p"),
      "--control-socket",
      sock,
    }
  end
  state.started_job = vim.fn.jobstart(args, { detach = true })
  if state.started_job <= 0 then
    error("failed to start " .. M.config.executable)
  end
  if not wait_for_socket() then
    error("groket did not create control socket: " .. sock)
  end
end

function M.connect()
  if M.connected() then
    return
  end
  local path = default_socket_path()
  if not uv.fs_stat(path) then
    error("groket control socket does not exist: " .. path)
  end
  local co = coroutine.running()
  if not co then
    error("groket.connect must run inside a coroutine")
  end
  local sock = uv.new_pipe(false)
  local connect_err
  sock:connect(path, function(err)
    connect_err = err
    vim.schedule(function()
      coroutine.resume(co)
    end)
  end)
  coroutine.yield()
  if connect_err then
    sock:close()
    error("connect failed: " .. tostring(connect_err))
  end
  state.sock = sock
  sock:read_start(on_read)
  local version = vim.version()
  M.request("initialize", {
    protocolVersion = 1,
    clientInfo = {
      name = "Neovim",
      version = string.format("%d.%d.%d", version.major, version.minor, version.patch),
    },
  })
end

local function ensure_connection(session)
  if M.connected() then
    return
  end
  local path = default_socket_path()
  if not uv.fs_stat(path) then
    if M.config.auto_start and session and vim.fn.isdirectory(session) == 1 then
      M.start_tui(session)
    else
      error("groket control socket does not exist: " .. path .. " (start the TUI first)")
    end
  end
  M.connect()
end

local function normalize_session(session)
  if session == nil or session == "" then
    error("session path or id required")
  end
  if vim.fn.isdirectory(session) == 1 then
    return vim.fn.fnamemodify(session, ":p")
  end
  return session
end

---@param buf integer
local function set_stale_status(buf)
  local notes = vim.b[buf].groket_notes_stale and " Notes changed" or ""
  local trace = vim.b[buf].groket_session_stale and " Trace changed" or ""
  vim.b[buf].groket_status = vim.trim(trace .. notes)
  -- Refresh winbar when stale flags flip (best-effort).
  pcall(vim.cmd.redrawstatus)
end

---Reload or warn when a remote change arrives (first transition only).
---@param buf integer
---@param kind string
local function on_remote_stale(buf, kind)
  if M.config.auto_refresh ~= false then
    M.refresh({ auto = true, buf = buf, reason = kind })
  else
    notify(
      kind .. " — R or <LocalLeader>r to reload",
      vim.log.levels.WARN
    )
  end
end

---Mark every line that belongs to a column-0 fenced block.
---Transcripts render as ```` ```markdown ```` fences whose bodies sit at column 0,
---so their text can imitate machine tags and structural headings; scanners must
---skip those lines. The delimiter lines themselves count as fenced.
---@param lines string[]
---@return boolean[] one flag per line
local function fence_map(lines)
  local flags = {}
  local open_len ---@type integer|nil
  for i, line in ipairs(lines) do
    local run = line:match("^(`+)")
    if open_len == nil then
      if run and #run >= 3 then
        open_len = #run
        flags[i] = true
      else
        flags[i] = false
      end
    else
      flags[i] = true
      if run and #run >= open_len then
        open_len = nil
      end
    end
  end
  return flags
end

M._fence_map = fence_map

function M._on_notification(method, params)
  local session = params.sessionId
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.b[buf].groket_session_id then
      if session == nil or session == vim.b[buf].groket_session_id then
        if method == "notes/changed" then
          -- Same revision as our last save/load: echo of our own upsert, not external.
          local rev = params.revision
          if rev ~= nil and rev == vim.b[buf].groket_notes_revision then
            vim.b[buf].groket_notes_stale = false
          else
            local was = vim.b[buf].groket_notes_stale
            vim.b[buf].groket_notes_stale = true
            if not was then
              on_remote_stale(buf, "Notes changed")
            end
          end
        elseif method == "session/changed" then
          local was = vim.b[buf].groket_session_stale
          vim.b[buf].groket_session_stale = true
          if not was then
            on_remote_stale(buf, "Session trace changed")
          end
        elseif method == "session/selected" and params.promptIndex ~= nil then
          local idx = tostring(params.promptIndex)
          local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
          local fences = fence_map(lines)
          for i, line in ipairs(lines) do
            -- Boundary after the index so "4" does not match "prompt-index=41".
            local hit = not fences[i]
              and (
                line:find("prompt%-index=" .. idx .. "%f[%D]", 1, false)
                or line == ("## Prompt " .. idx)
                or line == (":GROKET_PROMPT_INDEX: " .. idx)
              )
            if hit then
              local win = vim.fn.bufwinid(buf)
              if win ~= -1 then
                vim.api.nvim_win_set_cursor(win, { i, 0 })
              end
              break
            end
          end
        end
        set_stale_status(buf)
      end
    end
  end
end

---Parse ``<!-- groket:k=v k2=v2 -->`` machine tags (Markdown projection).
---Only column-0 comments count; indented transcript content must not match.
---@param line string
---@return table<string, string>|nil
local function parse_groket_comment(line)
  local inner = line:match("^<!%-%-%s*groket:([^>]-)%s*%-%->%s*$")
  if not inner then
    return nil
  end
  local meta = {}
  for token in inner:gmatch("%S+") do
    local key, value = token:match("^([%w%-_]+)=(.*)$")
    if key then
      meta[key] = value
    end
  end
  return meta
end

---Strip the 4-space field/transcript indent from a rendered Markdown body line.
---@param line string
---@return string
local function strip_md_fixed_line(line)
  if line:sub(1, 4) == "    " then
    return line:sub(5)
  end
  return line
end

---Markdown ATX heading level (``#`` count), or nil.
---@param line string
---@return integer|nil
local function md_heading_level(line)
  local hashes = line:match("^(#+)%s+")
  if not hashes then
    return nil
  end
  return #hashes
end

---Projection structure only — not user paste that starts with ``#``.
---Note titles are ``#### ``; fields are ``##### ``; sections ``##`` / ``###``.
---@param line string
---@return boolean
local function is_note_heading(line)
  return line:match("^####%s+") ~= nil and line:match("^#####") == nil
end

---@param line string
---@return boolean
local function is_field_heading(line)
  return line:match("^#####%s+") ~= nil
end

---A ``####``/``#####`` heading is projection structure only when a machine tag
---follows within two lines. User paste at column 0 inside a field body carries
---no tag and must not terminate the body.
---@param lines string[]
---@param row integer 1-based
---@param fences boolean[]
---@return boolean
local function is_structural_heading(lines, row, fences)
  if fences[row] then
    return false
  end
  local line = lines[row]
  if not (is_note_heading(line) or is_field_heading(line)) then
    return false
  end
  for i = row + 1, math.min(row + 2, #lines) do
    if not fences[i] then
      local meta = parse_groket_comment(lines[i])
      if meta and (meta["note-id"] or meta["field-id"]) then
        return true
      end
    end
  end
  return false
end

---Section boundary (``## Prompt N`` / ``### User``) outside fences.
---@param lines string[]
---@param row integer 1-based
---@param fences boolean[]
---@return boolean
local function is_section_heading(lines, row, fences)
  if fences[row] then
    return false
  end
  return lines[row]:match("^##%s+") ~= nil or lines[row]:match("^###%s+") ~= nil
end

---Scan upward for a groket HTML comment attribute (fenced lines skipped).
---@param lines string[]
---@param row integer 1-based
---@param name string
---@param fences boolean[]|nil
---@return string|nil
local function ancestor_meta(lines, row, name, fences)
  fences = fences or fence_map(lines)
  for i = math.min(row, #lines), 1, -1 do
    if not fences[i] then
      local meta = parse_groket_comment(lines[i])
      if meta and meta[name] then
        return meta[name]
      end
    end
  end
  return nil
end

---Resolve a machine attribute at *row* (prompt/turn scope).
---Tags sit on the line under headings (``## Prompt 1`` then ``<!-- groket:… -->``),
---so a cursor on the heading must look a short distance *down* as well as up.
---@param lines string[]
---@param row integer 1-based
---@param name string
---@return string|nil
local function meta_near_row(lines, row, name)
  local fences = fence_map(lines)
  local found = ancestor_meta(lines, row, name, fences)
  if found then
    return found
  end
  -- Immediate look-ahead only (heading → optional blank → tag); avoid stealing a
  -- later note from transcript lines above ``### Operator notes``.
  for i = row + 1, math.min(row + 2, #lines) do
    if not fences[i] then
      local meta = parse_groket_comment(lines[i])
      if meta and meta[name] then
        return meta[name]
      end
    end
  end
  return nil
end

---Note that owns *row*: the nearest structural ``####`` heading at or above it.
---Field headings and bodies belong to that heading; a ``###``/``##`` section
---boundary means the cursor sits outside the operator notes of this note.
---@param lines string[]
---@param row integer 1-based
---@param fences boolean[]|nil
---@return string|nil
local function note_id_at_row(lines, row, fences)
  fences = fences or fence_map(lines)
  for i = math.min(row, #lines), 1, -1 do
    if not fences[i] then
      if is_note_heading(lines[i]) then
        if is_structural_heading(lines, i, fences) then
          for k = i + 1, math.min(i + 2, #lines) do
            if not fences[k] then
              local meta = parse_groket_comment(lines[k])
              if meta and meta["note-id"] then
                return meta["note-id"]
              end
            end
          end
        end
      elseif is_section_heading(lines, i, fences) then
        return nil
      end
    end
  end
  return nil
end

---Locate the ``#### `` note heading span for *note_id*.
---@param lines string[]
---@param note_id string
---@param fences boolean[]|nil
---@return integer, integer
local function note_span_md(lines, note_id, fences)
  fences = fences or fence_map(lines)
  local tag_line
  for i, line in ipairs(lines) do
    if not fences[i] then
      local meta = parse_groket_comment(line)
      if meta and meta["note-id"] == note_id and not meta["field-id"] then
        tag_line = i
        break
      end
    end
  end
  if not tag_line then
    error("could not locate note " .. note_id)
  end
  local note_start
  for i = tag_line, 1, -1 do
    if not fences[i] and is_note_heading(lines[i]) then
      note_start = i
      break
    end
  end
  if not note_start then
    error("could not locate note heading for " .. note_id)
  end
  local note_end = #lines
  for i = note_start + 1, #lines do
    -- Sibling note or leaving Operator notes / prompt — not user ``#`` paste.
    local sibling = is_note_heading(lines[i]) and is_structural_heading(lines, i, fences)
    if sibling or is_section_heading(lines, i, fences) then
      note_end = i - 1
      break
    end
  end
  return note_start, note_end
end

---@param buf integer
---@param row integer 1-based cursor line
---@return table
local function note_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local fences = fence_map(lines)
  local note_id = note_id_at_row(lines, row, fences)
  if not note_id then
    error("cursor is not inside an operator note")
  end
  local note_start, note_end = note_span_md(lines, note_id, fences)
  -- turn-index lives on the prompt tag above the note heading.
  local turn_index = tonumber(ancestor_meta(lines, note_start, "turn-index", fences) or "0") or 0
  local event_text = ""
  local created_at = ""
  for i = note_start, math.min(note_start + 5, note_end) do
    if not fences[i] then
      local meta = parse_groket_comment(lines[i])
      if meta and meta["note-id"] == note_id and not meta["field-id"] then
        event_text = meta["event-indices"] or ""
        created_at = meta.created or ""
        break
      end
    end
  end
  local fields = vim.empty_dict()
  local i = note_start
  while i <= note_end do
    if not fences[i] and is_field_heading(lines[i]) then
      local field_id
      local body_start = i + 1
      -- Machine tag sits directly under the field heading.
      if body_start <= note_end and not fences[body_start] then
        local meta = parse_groket_comment(lines[body_start])
        if meta and meta["field-id"] then
          field_id = meta["field-id"]
          body_start = body_start + 1
          -- Exactly one blank separator; further blanks are part of the value.
          if body_start <= note_end and lines[body_start] == "" then
            body_start = body_start + 1
          end
        end
      end
      if field_id then
        local body_end = note_end
        for k = body_start, note_end do
          -- Next field / note / section — not a bare ``#`` paste inside the body.
          local structural = (is_field_heading(lines[k]) or is_note_heading(lines[k]))
            and is_structural_heading(lines, k, fences)
          if structural or is_section_heading(lines, k, fences) then
            body_end = k - 1
            break
          end
        end
        local body = {}
        for k = body_start, body_end do
          table.insert(body, strip_md_fixed_line(lines[k]))
        end
        -- One trailing blank belongs to the renderer's separator, the rest to the value.
        if #body > 0 and body[#body] == "" then
          table.remove(body)
        end
        fields[field_id] = table.concat(body, "\n")
        i = body_end + 1
      else
        i = i + 1
      end
    else
      i = i + 1
    end
  end
  local event_indices = {}
  for part in string.gmatch(event_text, "[^,]+") do
    local n = tonumber(vim.trim(part))
    if n then
      table.insert(event_indices, n)
    end
  end
  return {
    id = note_id,
    turnIndex = turn_index,
    fields = fields,
    eventIndices = event_indices,
    createdAt = created_at,
    updatedAt = os.date("!%Y-%m-%dT%H:%M:%S+00:00"),
  }
end

---Exposed for headless round-trip tests (``nvim --headless -l``).
M._note_at_row = note_at_row
M._parse_groket_comment = parse_groket_comment
M._note_id_at_row = function(lines, row)
  return note_id_at_row(lines, row)
end

local function prompt_index_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local raw = meta_near_row(lines, row, "prompt-index")
  if raw == nil then
    return nil
  end
  return tonumber(raw)
end

local function turn_index_at_row(buf, row)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local raw = meta_near_row(lines, row, "turn-index")
  if raw == nil then
    return nil
  end
  return tonumber(raw)
end

local function session_help()
  local ll = vim.g.maplocalleader
  if type(ll) ~= "string" or ll == "" then
    ll = "\\"
  end
  -- Display space localleader clearly.
  local show = (ll == " ") and "<Space>" or ll
  notify(
    string.format(
      "Groket: %sc save · %ss all · %sn new · %sk del · %so prompt · %sr refresh · %sh highlight (%s) · :w all",
      show,
      show,
      show,
      show,
      show,
      show,
      show,
      tostring(M.config.highlight or "soft")
    ),
    vim.log.levels.INFO
  )
end

-- Forward decl: map_buffer autocmds call this after it is assigned below.
local apply_window_chrome

local function map_buffer(buf)
  local function map(lhs, rhs, desc)
    vim.keymap.set("n", lhs, rhs, { buffer = buf, silent = true, desc = desc })
  end
  local function map_ni(lhs, rhs, desc)
    vim.keymap.set({ "n", "i" }, lhs, rhs, { buffer = buf, silent = true, desc = desc })
  end
  -- Avoid bare ``g`` (breaks gg). Prefer LocalLeader; keep ``R`` as fugitive-style refresh.
  map("R", function()
    M.refresh()
  end, "Groket: refresh session")
  map("<LocalLeader>r", function()
    M.refresh()
  end, "Groket: refresh session")
  map("<LocalLeader>o", function()
    M.open_prompt_at_cursor()
  end, "Groket: select prompt in TUI")
  map("<LocalLeader>c", function()
    M.save_note()
  end, "Groket: save note at cursor")
  map("<LocalLeader>n", function()
    M.new_note()
  end, "Groket: new note")
  map("<LocalLeader>k", function()
    M.delete_note()
  end, "Groket: delete note")
  map("<LocalLeader>s", function()
    M.save_all_notes()
  end, "Groket: save all notes")
  map("<LocalLeader>?", session_help, "Groket: session key help")
  map("<LocalLeader>h", function()
    M.cycle_highlight()
  end, "Groket: cycle highlight mode")
  map("<LocalLeader>O", function()
    M.outline()
  end, "Groket: session outline (prompts/notes)")
  map_ni("<C-s>", function()
    vim.cmd.stopinsert()
    M.save_note()
  end, "Groket: save note at cursor")

  local augroup = vim.api.nvim_create_augroup("groket_buf_" .. buf, { clear = true })
  -- :w / :write → upsert every note (Emacs C-x C-s parity); buffer is nofile.
  vim.api.nvim_create_autocmd("BufWriteCmd", {
    group = augroup,
    buffer = buf,
    desc = "Groket: save all notes on :w",
    callback = function()
      M.save_all_notes()
    end,
  })
  vim.api.nvim_create_autocmd({ "BufWinEnter", "BufEnter" }, {
    group = augroup,
    buffer = buf,
    desc = "Groket: session window chrome",
    callback = function()
      apply_window_chrome(buf)
    end,
  })
end

---Treesitter highlighting drives the fold expression; ``calm``/``none`` stop the
---parser and Neovim without a markdown parser has none at all, so folds fall
---back to ``manual`` rather than erroring on every redraw.
---@param buf integer
---@param winid integer
local function apply_fold_chrome(buf, winid)
  local mode = M.config.highlight or "soft"
  local ts_mode = mode ~= "calm" and mode ~= "none"
  local has_parser = false
  if ts_mode then
    local ok, parser = pcall(vim.treesitter.get_parser, buf)
    has_parser = ok and parser ~= nil
  end
  pcall(function()
    if has_parser then
      -- Treesitter folds for ## / ### (open by default; zM / za to collapse).
      vim.wo[winid].foldenable = true
      vim.wo[winid].foldmethod = "expr"
      vim.wo[winid].foldexpr = "v:lua.vim.treesitter.foldexpr()"
      vim.wo[winid].foldlevel = 99
    else
      vim.wo[winid].foldmethod = "manual"
    end
  end)
end

---Hide machine tags and keep pipe tables aligned in one window on *buf*.
---@param buf integer
---@param winid integer
local function apply_chrome_to_win(buf, winid)
  pcall(function()
    local st = vim.b[buf].groket_status
    if type(st) == "string" and st ~= "" then
      vim.wo[winid].winbar = "groket · " .. st .. " · \\? help"
    else
      vim.wo[winid].winbar = "groket · \\? help"
    end
  end)
  pcall(function()
    vim.wo[winid].conceallevel = 2
    -- Show tags under the cursor line so operators can inspect them in any mode.
    vim.wo[winid].concealcursor = ""
    vim.wo[winid].wrap = false
  end)
  apply_fold_chrome(buf, winid)
  -- Conceal <!-- groket:… --> machine lines (still in buffer for parse/save).
  -- Match ids are window scoped: two windows on one buffer each own their match.
  pcall(vim.api.nvim_win_call, winid, function()
    local prev = vim.w[winid].groket_conceal_id
    if prev then
      pcall(vim.fn.matchdelete, prev)
      vim.w[winid].groket_conceal_id = nil
    end
    vim.w[winid].groket_conceal_id = vim.fn.matchadd(
      "Conceal",
      [[^<!-- groket:.\{-}-->\s*$]],
      10,
      -1,
      { conceal = "" }
    )
  end)
end

---Apply window chrome to every window showing *buf*; each keeps its own match id.
---@param buf integer
apply_window_chrome = function(buf)
  if not vim.api.nvim_buf_is_valid(buf) then
    return
  end
  for _, winid in ipairs(vim.fn.win_findbuf(buf)) do
    apply_chrome_to_win(buf, winid)
  end
  local mode = M.config.highlight or "soft"
  if mode == "calm" or mode == "none" then
    pcall(vim.treesitter.stop, buf)
  end
end

local CODE_BLOCK_GROUPS = {
  "@markup.raw.block.markdown",
  "markdownCodeBlock",
  "RenderMarkdownCode",
  "RenderMarkdownCodeBorder",
  "RenderMarkdownCodeInfo",
}

---Definitions captured before the first quieting, keyed by group name.
local saved_code_block_hl = nil ---@type table<string, table>|nil

---Put the colorscheme's code-block groups back; they are global, so every other
---Markdown buffer shares them.
local function restore_code_block_bg()
  if not saved_code_block_hl then
    return
  end
  for group, def in pairs(saved_code_block_hl) do
    pcall(vim.api.nvim_set_hl, 0, group, def)
  end
  saved_code_block_hl = nil
end

---Clear code-block grey wash (transcript is a large ```markdown fence).
---Highlight groups are global in Neovim; we only touch background, not colors.
---@param buf integer
local function quiet_code_block_bg(buf)
  pcall(function()
    vim.b[buf].render_markdown = vim.tbl_deep_extend("force", vim.b[buf].render_markdown or {}, {
      code = {
        width = "full",
        border = "none",
        disable_background = true,
      },
    })
  end)
  if not saved_code_block_hl then
    local saved = {}
    for _, group in ipairs(CODE_BLOCK_GROUPS) do
      -- ``link = true`` keeps a linked group linked when restored; a group that
      -- does not exist yet comes back as the empty definition it started as.
      local ok, def = pcall(vim.api.nvim_get_hl, 0, { name = group, link = true })
      saved[group] = (ok and type(def) == "table") and def or {}
    end
    saved_code_block_hl = saved
  end
  for _, group in ipairs(CODE_BLOCK_GROUPS) do
    pcall(vim.api.nvim_set_hl, 0, group, { bg = "NONE", default = false })
  end
end

---Session buffer highlighting. Default soft: nested MD + quieter chrome.
---@param buf integer
local function apply_highlight(buf)
  local mode = M.config.highlight or "soft"
  if mode == "none" then
    restore_code_block_bg()
    pcall(function()
      vim.bo[buf].filetype = ""
    end)
    pcall(vim.treesitter.stop, buf)
    pcall(function()
      vim.bo[buf].syntax = ""
    end)
    return
  end
  if mode == "calm" then
    restore_code_block_bg()
    pcall(function()
      vim.bo[buf].filetype = "markdown"
    end)
    pcall(vim.treesitter.stop, buf)
    pcall(function()
      vim.cmd("syntax enable")
      vim.bo[buf].syntax = "markdown"
    end)
    return
  end
  -- soft / full: treesitter so ```markdown bodies inject tables and code.
  -- soft and full both use treesitter; soft only differs by default config
  -- intent (callers may still set highlight=soft). Code-block bg cleared always.
  pcall(function()
    vim.bo[buf].filetype = "markdown"
  end)
  pcall(function()
    vim.treesitter.start(buf, "markdown")
  end)
  quiet_code_block_bg(buf)
end

local HIGHLIGHT_CYCLE = { "soft", "calm", "full", "none" }

---Re-apply highlight + window chrome to every open groket session buffer.
local function reapply_highlight_all_sessions()
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and vim.b[buf].groket_session_id then
      apply_highlight(buf)
      apply_window_chrome(buf)
    end
  end
end

---Set highlight mode and re-apply to open session buffers.
---@param mode string
function M.set_highlight(mode)
  local allowed = { soft = true, calm = true, full = true, none = true }
  if not allowed[mode] then
    error("highlight must be soft|calm|full|none, got " .. tostring(mode))
  end
  M.config.highlight = mode
  reapply_highlight_all_sessions()
  notify("highlight = " .. mode)
end

---Cycle soft → calm → full → none → soft.
---@return string new mode
function M.cycle_highlight()
  local cur = M.config.highlight or "soft"
  local idx = 1
  for i, name in ipairs(HIGHLIGHT_CYCLE) do
    if name == cur then
      idx = i
      break
    end
  end
  local next_mode = HIGHLIGHT_CYCLE[(idx % #HIGHLIGHT_CYCLE) + 1]
  M.set_highlight(next_mode)
  return next_mode
end

---Location-list outline of prompts and operator notes in the session buffer.
function M.outline()
  local buf = vim.api.nvim_get_current_buf()
  if not vim.b[buf].groket_session_id then
    error("not a groket session buffer")
  end
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local fences = fence_map(lines)
  local entries = {}
  for i, line in ipairs(lines) do
    local text ---@type string|nil
    if fences[i] then
      text = nil
    elseif line:match("^## Prompt ") then
      text = line
    elseif line:match("^### ") then
      -- User / Assistant / Operator notes under a prompt
      text = "  " .. line
    elseif line:match("^#### ") and not line:match("^#####") then
      text = "    " .. line
    end
    if text then
      table.insert(entries, {
        bufnr = buf,
        lnum = i,
        col = 1,
        text = text,
      })
    end
  end
  if #entries == 0 then
    notify("no outline headings in this buffer", vim.log.levels.WARN)
    return
  end
  vim.fn.setloclist(0, entries, "r")
  vim.cmd("lopen 14")
  notify(string.format("outline: %d headings (Enter to jump, :lclose to dismiss)", #entries))
end

local function apply_document(buf, text, session_id, revision, reference)
  local lines = vim.split(text, "\n", { plain = true })
  if #lines > 0 and lines[#lines] == "" then
    table.remove(lines)
  end
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modifiable = true
  vim.bo[buf].buflisted = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.b[buf].groket_session_id = session_id
  vim.b[buf].groket_session_reference = reference
  vim.b[buf].groket_notes_revision = revision
  vim.b[buf].groket_notes_stale = false
  vim.b[buf].groket_session_stale = false
  set_stale_status(buf)
  apply_highlight(buf)
  vim.bo[buf].modified = false
  pcall(vim.api.nvim_buf_set_name, buf, "groket://" .. session_id)
  map_buffer(buf)
  -- Window opts if already displayed.
  apply_window_chrome(buf)
  -- Snapshot note ids from this projection; typed machine tags are not upserted.
  local rendered = {}
  local fences = fence_map(lines)
  for i, line in ipairs(lines) do
    local meta = (not fences[i]) and parse_groket_comment(line) or nil
    if meta and meta["note-id"] and not meta["field-id"] then
      rendered[meta["note-id"]] = true
    end
  end
  vim.b[buf].groket_rendered_note_ids = rendered
end

M._apply_document = apply_document

---Move the cursor onto a note after create/render (first field body, else heading).
---@param buf integer
---@param note_id string
local function jump_to_note(buf, note_id)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local fences = fence_map(lines)
  local note_heading_row ---@type integer|nil
  local first_field_body ---@type integer|nil
  for i, line in ipairs(lines) do
    local meta = (not fences[i]) and parse_groket_comment(line) or nil
    if meta and meta["note-id"] == note_id and not meta["field-id"] then
      for h = i, 1, -1 do
        if not fences[h] and md_heading_level(lines[h]) then
          note_heading_row = h
          break
        end
      end
    elseif meta and meta["note-id"] == note_id and meta["field-id"] and not first_field_body then
      local body = i + 1
      while body <= #lines and lines[body]:match("^%s*$") do
        body = body + 1
      end
      -- Empty field: still land on the blank line under the field tag.
      first_field_body = math.min(body, #lines)
    end
  end
  local target = first_field_body or note_heading_row
  if not target then
    return
  end
  local win = vim.fn.bufwinid(buf)
  if win ~= -1 then
    pcall(vim.api.nvim_win_set_cursor, win, { target, 0 })
  end
end

M._jump_to_note = jump_to_note

local function run(fn)
  local co = coroutine.create(function()
    local ok, err = pcall(fn)
    if not ok then
      notify(tostring(err), vim.log.levels.ERROR)
    end
  end)
  local ok, err = coroutine.resume(co)
  if not ok then
    notify(tostring(err), vim.log.levels.ERROR)
  end
end

local function session_entry_label(entry)
  local title = entry.title or entry.label or ""
  local session_id = entry.sessionId or ""
  local head = (title ~= "" and title) or session_id
  local status = entry.status or "—"
  local origin = entry.origin or ""
  local model = entry.model or ""
  -- Compact, scannable: [status] origin · title · model · id
  local bits = {
    string.format("%-8s", status),
    (origin ~= "" and origin) or "work",
    head,
  }
  if model ~= "" then
    table.insert(bits, model)
  end
  if session_id ~= "" and session_id ~= head then
    table.insert(bits, session_id)
  end
  return table.concat(bits, "  ·  ")
end

local function session_entry_path(entry)
  return entry.path or entry.sessionId
end

local function session_entry_search_text(entry)
  return table.concat({
    entry.sessionId or "",
    entry.path or "",
    entry.title or "",
    entry.label or "",
    entry.model or "",
    entry.status or "",
    entry.outcome or "",
    entry.origin or "",
  }, " "):lower()
end

---@class groket.PickItem
---@field label string
---@field path string
---@field entry table
---@field search string

---@param sessions table[]
---@return groket.PickItem[]
local function sessions_to_items(sessions)
  local items = {}
  local seen = {}
  for _, entry in ipairs(sessions) do
    local label = session_entry_label(entry)
    local key = label
    local n = 2
    while seen[key] do
      key = label .. " (" .. tostring(n) .. ")"
      n = n + 1
    end
    seen[key] = true
    table.insert(items, {
      label = key,
      path = session_entry_path(entry),
      entry = entry,
      search = session_entry_search_text(entry),
    })
  end
  return items
end

local function fuzzy_score(haystack, needle)
  if needle == nil or needle == "" then
    return 1
  end
  needle = needle:lower()
  haystack = haystack:lower()
  if haystack:find(needle, 1, true) then
    return 1000 - (haystack:find(needle, 1, true) or 0)
  end
  -- subsequence match (fzf-ish)
  local hi = 1
  local score = 0
  local last = 0
  for i = 1, #needle do
    local ch = needle:sub(i, i)
    local found = haystack:find(ch, hi, true)
    if not found then
      return nil
    end
    if last > 0 and found == last + 1 then
      score = score + 5
    else
      score = score + 1
    end
    last = found
    hi = found + 1
  end
  return score
end

---@param items groket.PickItem[]
---@param query string
---@return groket.PickItem[]
local function filter_items(items, query)
  query = vim.trim(query or "")
  if query == "" then
    return items
  end
  local scored = {}
  for _, item in ipairs(items) do
    local s = fuzzy_score(item.search .. " " .. item.label, query)
    if s then
      table.insert(scored, { s = s, item = item })
    end
  end
  table.sort(scored, function(a, b)
    if a.s == b.s then
      return a.item.label < b.item.label
    end
    return a.s > b.s
  end)
  local out = {}
  for _, row in ipairs(scored) do
    table.insert(out, row.item)
  end
  return out
end

---Stock Neovim picker (and any `vim.ui.select` override, e.g. dressing / telescope-ui-select).
---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_ui_select(items, on_choice)
  local labels = {}
  local by = {}
  for _, item in ipairs(items) do
    table.insert(labels, item.label)
    by[item.label] = item
  end
  vim.ui.select(labels, {
    prompt = "Groket session",
    kind = "groket_session",
    format_item = function(label)
      return label
    end,
  }, function(choice)
    on_choice(choice and by[choice] or nil)
  end)
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_telescope(items, on_choice)
  local pickers = require("telescope.pickers")
  local finders = require("telescope.finders")
  local conf = require("telescope.config").values
  local actions = require("telescope.actions")
  local action_state = require("telescope.actions.state")
  pickers
    .new({}, {
      prompt_title = "Groket sessions",
      finder = finders.new_table({
        results = items,
        entry_maker = function(item)
          return {
            value = item,
            display = item.label,
            ordinal = item.search .. " " .. item.label,
          }
        end,
      }),
      sorter = conf.generic_sorter({}),
      attach_mappings = function(prompt_bufnr, _)
        actions.select_default:replace(function()
          local selection = action_state.get_selected_entry()
          actions.close(prompt_bufnr)
          on_choice(selection and selection.value or nil)
        end)
        return true
      end,
    })
    :find()
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_fzf_lua(items, on_choice)
  local fzf = require("fzf-lua")
  local labels = {}
  local by = {}
  for _, item in ipairs(items) do
    table.insert(labels, item.label)
    by[item.label] = item
  end
  fzf.fzf_exec(labels, {
    prompt = "Groket> ",
    actions = {
      ["default"] = function(selected)
        local label = selected and selected[1]
        on_choice(label and by[label] or nil)
      end,
    },
  })
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_mini(items, on_choice)
  local mini_pick = require("mini.pick")
  local labels = vim.tbl_map(function(i)
    return i.label
  end, items)
  local by = {}
  for _, item in ipairs(items) do
    by[item.label] = item
  end
  mini_pick.start({
    source = {
      items = labels,
      name = "Groket sessions",
      choose = function(label)
        on_choice(label and by[label] or nil)
      end,
    },
  })
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_with_snacks(items, on_choice)
  local snacks = require("snacks")
  local rows = {}
  for _, item in ipairs(items) do
    table.insert(rows, {
      text = item.label,
      item = item,
    })
  end
  snacks.picker.pick({
    items = rows,
    format = "text",
    confirm = function(picker, item)
      picker:close()
      on_choice(item and item.item or nil)
    end,
  })
end

---@param items groket.PickItem[]
---@param on_choice fun(item: groket.PickItem|nil)
local function pick_sessions(items, on_choice)
  local mode = M.config.picker or "auto"
  if mode == "select" or mode == "float" then
    -- "float" kept as an alias for older configs; stock select is the reliable UI.
    pick_with_ui_select(items, on_choice)
    return
  end
  local function try(name, fn)
    if mode ~= "auto" and mode ~= name then
      return false
    end
    local ok = pcall(fn)
    return ok
  end
  if
    try("snacks", function()
      pick_with_snacks(items, on_choice)
    end)
  then
    return
  end
  if
    try("telescope", function()
      pick_with_telescope(items, on_choice)
    end)
  then
    return
  end
  if
    try("fzf-lua", function()
      pick_with_fzf_lua(items, on_choice)
    end)
  then
    return
  end
  if
    try("mini", function()
      pick_with_mini(items, on_choice)
    end)
  then
    return
  end
  pick_with_ui_select(items, on_choice)
end

---List catalog sessions from the running TUI.
---@param query string|nil
---@param limit integer|nil
---@return table result
function M.list_sessions(query, limit)
  local co = coroutine.running()
  if not co then
    error("groket.list_sessions must run inside a coroutine")
  end
  ensure_connection(nil)
  local params = { query = query or "" }
  if limit ~= nil then
    params.limit = limit
  end
  return M.request("session/list", params)
end

local function render_params(session)
  -- Neovim client only implements Markdown machine tags.
  return {
    session = session,
    format = "markdown",
  }
end

function M.open_session(session, prompt_index)
  run(function()
    local reference = normalize_session(session)
    ensure_connection(reference)
    local result = M.request("session/render", render_params(reference))
    local buf = vim.api.nvim_create_buf(true, true)
    apply_document(buf, result.text, result.sessionId, result.notesRevision, reference)
    -- Show the rendered buffer before asking the TUI to follow; a session/open
    -- failure then leaves a usable buffer instead of an orphaned one.
    vim.api.nvim_set_current_buf(buf)
    apply_window_chrome(buf)
    if prompt_index ~= nil then
      -- Local jump (notification may also move the cursor).
      local idx = tostring(prompt_index)
      local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
      local fences = fence_map(lines)
      for i, line in ipairs(lines) do
        if
          not fences[i]
          and (
            line:find("prompt%-index=" .. idx .. "%f[%D]", 1, false)
            or line == ("## Prompt " .. idx)
          )
        then
          pcall(vim.api.nvim_win_set_cursor, 0, { i, 0 })
          break
        end
      end
    end
    local params = { session = reference }
    if prompt_index ~= nil then
      params.promptIndex = prompt_index
    end
    local opened, open_err = pcall(M.request, "session/open", params)
    if not opened then
      notify("session/open failed: " .. tostring(open_err), vim.log.levels.WARN)
    end
    notify(
      "opened "
        .. result.sessionId
        .. " — LocalLeader ? for keys (\\c save · \\n new note)"
    )
  end)
end

---Pick a catalog session and open it (``vim.ui.select`` or ecosystem picker).
---@param query string|nil optional pre-filter sent to the server
function M.find_session(query)
  run(function()
    -- Pull full catalog when query empty so the picker can filter locally;
    -- server pre-filter still applies when the user passes a seed query.
    local result = M.list_sessions(query, query and query ~= "" and 200 or 500)
    local sessions = result.sessions or {}
    if #sessions == 0 then
      local suffix = (query and query ~= "") and (" for " .. vim.inspect(query)) or ""
      error("no sessions matched" .. suffix .. " (is the TUI running with a catalog?)")
    end
    local items = sessions_to_items(sessions)
    vim.schedule(function()
      pick_sessions(items, function(item)
        if item and item.path then
          M.open_session(item.path)
        end
      end)
    end)
  end)
end

---Session browser buffer with live `/` filter and open keys.
---@param query string|nil
function M.show_sessions(query)
  run(function()
    local result = M.list_sessions(query, 500)
    local sessions = result.sessions or {}
    local items = sessions_to_items(sessions)
    vim.schedule(function()
      local buf = vim.api.nvim_create_buf(true, true)
      vim.bo[buf].buftype = "nofile"
      vim.bo[buf].bufhidden = "wipe"
      vim.bo[buf].swapfile = false
      vim.bo[buf].modifiable = true

      local function paint(filter)
        local filtered = filter_items(items, filter or "")
        local lines = {
          string.format(
            "Groket  %d/%d  f filter · <CR>/o open · P pick · r refresh · q quit%s",
            #filtered,
            #items,
            (filter and filter ~= "") and ("  ·  /" .. filter) or ""
          ),
          "",
        }
        local paths = {}
        if #filtered == 0 then
          table.insert(lines, "(no matches)")
        else
          for i, item in ipairs(filtered) do
            table.insert(lines, item.label)
            paths[i + 2] = item.path
          end
        end
        vim.bo[buf].modifiable = true
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
        vim.bo[buf].modifiable = false
        vim.b[buf].groket_session_paths = paths
        vim.b[buf].groket_session_filter = filter or ""
      end

      paint(query or "")
      vim.bo[buf].filetype = "groket-sessions"
      pcall(vim.api.nvim_buf_set_name, buf, "groket://sessions")

      local function open_row()
        local row = vim.api.nvim_win_get_cursor(0)[1]
        local path = vim.b[buf].groket_session_paths and vim.b[buf].groket_session_paths[row]
        if path then
          M.open_session(path)
        else
          notify("no session on this line", vim.log.levels.WARN)
        end
      end

      vim.keymap.set("n", "<CR>", open_row, { buffer = buf, desc = "Groket: open session" })
      vim.keymap.set("n", "o", open_row, { buffer = buf, desc = "Groket: open session" })
      vim.keymap.set("n", "q", "<cmd>bd!<cr>", { buffer = buf, desc = "Groket: close browser" })
      vim.keymap.set("n", "r", function()
        M.show_sessions(vim.b[buf].groket_session_filter)
      end, { buffer = buf, desc = "Groket: reload catalog" })
      vim.keymap.set("n", "f", function()
        vim.ui.input({
          prompt = "Filter sessions: ",
          default = vim.b[buf].groket_session_filter or "",
        }, function(input)
          if input ~= nil then
            paint(input)
          end
        end)
      end, { buffer = buf, desc = "Groket: filter list" })
      vim.keymap.set("n", "/", function()
        vim.ui.input({
          prompt = "Filter sessions: ",
          default = vim.b[buf].groket_session_filter or "",
        }, function(input)
          if input ~= nil then
            paint(input)
          end
        end)
      end, { buffer = buf, desc = "Groket: filter list" })
      -- Avoid bare ``g`` (``gg``) and ``p`` (paste); use ``P`` for fuzzy pick.
      vim.keymap.set("n", "P", function()
        M.find_session(vim.b[buf].groket_session_filter)
      end, { buffer = buf, desc = "Groket: pick session" })

      vim.api.nvim_set_current_buf(buf)
      notify(string.format("listed %d session(s) — <CR> open · f filter · P pick", #items))
    end)
  end)
end

---@param opts { auto?: boolean, buf?: integer, reason?: string }|nil
function M.refresh(opts)
  opts = opts or {}
  local auto = opts.auto == true
  run(function()
    local buf = opts.buf or vim.api.nvim_get_current_buf()
    if not vim.api.nvim_buf_is_valid(buf) then
      return
    end
    local reference = vim.b[buf].groket_session_reference or vim.b[buf].groket_session_id
    if not reference then
      error("not a groket session buffer")
    end
    local function warn_unsaved()
      notify(
        (opts.reason or "Remote change") .. " — unsaved note edits; save or R to reload",
        vim.log.levels.WARN
      )
    end
    if vim.bo[buf].modified then
      if auto then
        warn_unsaved()
        return
      end
      local choice = vim.fn.confirm("Discard unsaved note edits?", "&Yes\n&No", 2)
      if choice ~= 1 then
        return
      end
    end
    ensure_connection(reference)
    local result = M.request("session/render", render_params(reference))
    if not vim.api.nvim_buf_is_valid(buf) then
      return
    end
    if auto and vim.bo[buf].modified then
      -- Edited during the render round trip: keep the stale marker, drop the reload.
      if opts.reason == "Notes changed" then
        vim.b[buf].groket_notes_stale = true
      else
        vim.b[buf].groket_session_stale = true
      end
      set_stale_status(buf)
      warn_unsaved()
      return
    end
    local win = vim.fn.bufwinid(buf)
    local row = 1
    if win ~= -1 then
      row = vim.api.nvim_win_get_cursor(win)[1]
    end
    apply_document(buf, result.text, result.sessionId, result.notesRevision, reference)
    local line_count = vim.api.nvim_buf_line_count(buf)
    if win ~= -1 then
      pcall(vim.api.nvim_win_set_cursor, win, { math.min(row, line_count), 0 })
    end
    if auto then
      notify("Reloaded (" .. (opts.reason or "remote change") .. ")")
    end
  end)
end

function M.open_prompt_at_cursor()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    local row = vim.api.nvim_win_get_cursor(0)[1]
    local prompt_index = prompt_index_at_row(buf, row)
    if prompt_index == nil then
      error("cursor is not inside a prompt")
    end
    ensure_connection(reference)
    M.request("session/open", { session = reference, promptIndex = prompt_index })
    notify("selected prompt " .. tostring(prompt_index))
  end)
end

function M.save_note()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    local row = vim.api.nvim_win_get_cursor(0)[1]
    local note = note_at_row(buf, row)
    local allowed = vim.b[buf].groket_rendered_note_ids
    if type(allowed) ~= "table" or not allowed[note.id] then
      error("note " .. note.id .. " is not part of this session projection")
    end
    ensure_connection(reference)
    local result = M.request("notes/upsert", {
      session = reference,
      expectedRevision = vim.b[buf].groket_notes_revision,
      note = note,
    })
    vim.b[buf].groket_notes_revision = result.revision
    vim.b[buf].groket_notes_stale = false
    -- Leave modified true: other notes in this buffer may still be dirty.
    -- Clear only from save_all_notes / apply_document.
    set_stale_status(buf)
    notify("saved note " .. note.id)
  end)
end

function M.new_note()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    -- Creating a note re-renders the whole projection; typed edits would vanish.
    if vim.bo[buf].modified then
      error("unsaved note edits — save with :w (or \\s) before creating a note")
    end
    local row = vim.api.nvim_win_get_cursor(0)[1]
    local turn_index = turn_index_at_row(buf, row)
    local prompt_index = prompt_index_at_row(buf, row)
    -- Prompt/turn index 0 is valid; only nil means "not in a prompt".
    if turn_index == nil or prompt_index == nil then
      error("cursor is not inside a prompt")
    end
    ensure_connection(reference)
    local listed = M.request("notes/list", { session = reference })
    local fields = vim.empty_dict()
    for _, spec in ipairs((listed.schema and listed.schema.fields) or {}) do
      if spec.id then
        fields[spec.id] = ""
      end
    end
    local timestamp = os.date("!%Y-%m-%dT%H:%M:%S+00:00")
    local note_id = "n-"
      .. vim.fn.sha256(tostring(uv.now()) .. tostring(math.random(1, 1000000000))):sub(1, 12)
    local result = M.request("notes/upsert", {
      session = reference,
      expectedRevision = vim.b[buf].groket_notes_revision,
      note = {
        id = note_id,
        turnIndex = turn_index,
        fields = fields,
        eventIndices = {},
        createdAt = timestamp,
        updatedAt = timestamp,
      },
    })
    vim.b[buf].groket_notes_revision = result.revision
    local rendered = M.request("session/render", render_params(reference))
    if not vim.api.nvim_buf_is_valid(buf) then
      return
    end
    apply_document(buf, rendered.text, rendered.sessionId, rendered.notesRevision, reference)
    jump_to_note(buf, note_id)
    notify("created note " .. note_id)
  end)
end

function M.delete_note()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local win = vim.api.nvim_get_current_win()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    -- Deleting re-renders the whole projection; typed edits would vanish.
    if vim.bo[buf].modified then
      error("unsaved note edits — save with :w (or \\s) before deleting a note")
    end
    local keep_row = vim.api.nvim_win_get_cursor(win)[1]
    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    local note_id = note_id_at_row(lines, keep_row)
    if not note_id then
      error("cursor is not inside an operator note")
    end
    local choice = vim.fn.confirm("Delete note " .. note_id .. "?", "&Yes\n&No", 2)
    if choice ~= 1 then
      return
    end
    ensure_connection(reference)
    local result = M.request("notes/delete", {
      session = reference,
      expectedRevision = vim.b[buf].groket_notes_revision,
      noteId = note_id,
    })
    vim.b[buf].groket_notes_revision = result.revision
    local rendered = M.request("session/render", render_params(reference))
    if not vim.api.nvim_buf_is_valid(buf) then
      return
    end
    apply_document(buf, rendered.text, rendered.sessionId, rendered.notesRevision, reference)
    -- The originating window may have moved on during the two round trips.
    if vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == buf then
      local line_count = vim.api.nvim_buf_line_count(buf)
      pcall(vim.api.nvim_win_set_cursor, win, { math.min(keep_row, line_count), 0 })
    end
    notify("deleted note " .. note_id)
  end)
end

function M.save_all_notes()
  run(function()
    local buf = vim.api.nvim_get_current_buf()
    local reference = vim.b[buf].groket_session_reference
    if not reference then
      error("not a groket session buffer")
    end
    ensure_connection(reference)
    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    local fences = fence_map(lines)
    local allowed = vim.b[buf].groket_rendered_note_ids
    if type(allowed) ~= "table" then
      allowed = {}
    end
    local rows = {}
    local seen = {}
    local skipped = {}
    for i, line in ipairs(lines) do
      -- Transcript text at column 0 can imitate a note tag; only real ones count.
      local meta = (not fences[i]) and parse_groket_comment(line) or nil
      local note_id = meta and meta["note-id"]
      if note_id and not meta["field-id"] and not seen[note_id] then
        seen[note_id] = true
        if allowed[note_id] then
          table.insert(rows, i)
        else
          table.insert(skipped, note_id)
        end
      end
    end
    for _, row in ipairs(rows) do
      local note = note_at_row(buf, row)
      local result = M.request("notes/upsert", {
        session = reference,
        expectedRevision = vim.b[buf].groket_notes_revision,
        note = note,
      })
      vim.b[buf].groket_notes_revision = result.revision
    end
    vim.bo[buf].modified = false
    vim.b[buf].groket_notes_stale = false
    set_stale_status(buf)
    local msg = "saved " .. tostring(#rows) .. " note(s)"
    if #skipped > 0 then
      msg = msg .. "; skipped unknown id(s): " .. table.concat(skipped, ", ")
    end
    notify(msg)
  end)
end

---lhs strings this plugin currently owns, so a later ``setup()`` can move them.
local bound_global_keys = {} ---@type string[]

local function bind_global_keys()
  for _, lhs in ipairs(bound_global_keys) do
    pcall(vim.keymap.del, "n", lhs)
  end
  bound_global_keys = {}
  local keys = M.config.keys
  if type(keys) ~= "table" then
    return
  end
  local maps = {
    find = { rhs = function()
      M.find_session()
    end, desc = "Groket: fuzzy find session" },
    list = { rhs = function()
      M.show_sessions()
    end, desc = "Groket: session browser" },
    refresh = { rhs = function()
      M.refresh()
    end, desc = "Groket: refresh session buffer" },
    connect = { rhs = function()
      run(function()
        M.connect()
        notify("connected to " .. default_socket_path())
      end)
    end, desc = "Groket: connect control socket" },
  }
  for name, spec in pairs(maps) do
    local lhs = keys[name]
    -- ``false`` disables a default map.
    if type(lhs) == "string" and lhs ~= "" then
      vim.keymap.set("n", lhs, spec.rhs, { silent = true, desc = spec.desc })
      table.insert(bound_global_keys, lhs)
    end
  end
end

function M.setup(opts)
  M.config = vim.tbl_deep_extend("force", M.config, opts or {})
  vim.g.groket_setup_done = true
  -- Note ids must differ between Neovim instances started the same millisecond.
  pcall(function()
    local seed = (uv.hrtime() % 2147483647) + vim.fn.getpid()
    math.randomseed(seed % 2147483647)
  end)
  if vim.g.groket_commands_registered then
    bind_global_keys()
    return
  end
  vim.g.groket_commands_registered = true
  vim.api.nvim_create_user_command("GroketConnect", function()
    run(function()
      M.connect()
      notify("connected to " .. default_socket_path())
    end)
  end, {})
  vim.api.nvim_create_user_command("GroketDisconnect", function()
    M.disconnect()
    notify("disconnected")
  end, {})
  vim.api.nvim_create_user_command("GroketOpenSession", function(cmd)
    local args = vim.split(cmd.args, "%s+", { trimempty = true })
    if #args == 0 then
      notify("usage: :GroketOpenSession {session-path-or-id} [prompt-index]", vim.log.levels.ERROR)
      return
    end
    local prompt = args[2] and tonumber(args[2]) or nil
    M.open_session(args[1], prompt)
  end, { nargs = "+", complete = "dir" })
  vim.api.nvim_create_user_command("GroketSessions", function(cmd)
    local query = vim.trim(cmd.args or "")
    M.show_sessions(query ~= "" and query or nil)
  end, { nargs = "*" })
  vim.api.nvim_create_user_command("GroketFindSession", function(cmd)
    local query = vim.trim(cmd.args or "")
    M.find_session(query ~= "" and query or nil)
  end, { nargs = "*" })
  vim.api.nvim_create_user_command("GroketRefresh", function()
    M.refresh()
  end, {})
  vim.api.nvim_create_user_command("GroketHighlight", function(cmd)
    local arg = vim.trim(cmd.args or "")
    if arg == "" then
      M.cycle_highlight()
      return
    end
    M.set_highlight(arg)
  end, {
    nargs = "?",
    complete = function()
      return { "soft", "calm", "full", "none" }
    end,
  })
  vim.api.nvim_create_user_command("GroketOutline", function()
    M.outline()
  end, {})
  vim.api.nvim_create_user_command("GroketSaveNote", function()
    M.save_note()
  end, {})
  vim.api.nvim_create_user_command("GroketSaveAllNotes", function()
    M.save_all_notes()
  end, {})
  vim.api.nvim_create_user_command("GroketNewNote", function()
    M.new_note()
  end, {})
  vim.api.nvim_create_user_command("GroketDeleteNote", function()
    M.delete_note()
  end, {})
  vim.api.nvim_create_user_command("GroketOpenPrompt", function()
    M.open_prompt_at_cursor()
  end, {})
  bind_global_keys()
end

return M
