# Knowledge-base connections

The management page has **按分类** (the existing manual/business categories) and **按关联群** views.
In the cluster view, every KB is a floating bubble with an always-available connection point. Drag that point
to another KB (or click/tap the two points in order) to open a dialog with an **optional** relationship comment
(0–1,000 characters). Nothing is saved until confirmation. Use **连接知识库** or a library card's **关联**
button as an alternative. Connections are undirected, one per pair, and a KB may connect to
many others. Saving the reverse pair updates the existing summary. Click a relationship or line to edit it;
remove a connection directly on the canvas: hover or select a wire, then use its delete control. The same action
is available in the comment list. Keyboard users can focus a wire and press Enter to reach its edit/delete controls.
KB contents are preserved when removing an edge.

Clusters are the connected components of those edges, not separate folders. Isolated KBs remain visible.
The shared canvas contains every KB, including isolated ones, so users can connect across clusters. Drag bubbles
to reposition, drag the background to pan, and use zoom/fit/arrange controls. Cluster chips focus a group.
Search highlights and locates matching KBs. Comments remain editable in the collapsible relationship list or by
selecting a line. All actions update without a page reload.

Bubble diameter grows on a capped logarithmic scale: manual libraries use the number of completed documents
in the library and its child collections; business libraries use completed tracking-table rows. Counts are
aggregated by the database, displayed on the bubbles, and do not require loading raw records. Curved links
attach to each bubble's actual radius and update during dragging, floating, settling and zooming. Nearby
endpoints use subtle curves; lines route around intervening bubbles when needed.

Bubbles float gently with ports and links kept in sync. After a new connection, the endpoint with fewer
connections glides toward the more connected endpoint and settles into a free nearby position. For equal
degrees the dragged KB moves. Existing comments/edges retain their identity; dragging an already connected pair
opens its current comment. Moving KBs pauses animation during interaction; reduced-motion preferences disable
floating and settling animations. Layout positions are local to the current page, while relationships are saved.

Only top-level KBs are linkable; their child document collections inherit navigation from the parent.
Both endpoints must be manageable by the user to create, edit, delete or see a connection in this management
view. Deleting either KB cascades its edges. The `KnowledgeBaseLink` model enforces a canonical ordered pair,
uniqueness, and no self-links. Migration `0045_knowledgebaselink` creates the table;
`0046_knowledgebaselink_optional_summary` makes comments optional without replacing existing comments.

## Agent behavior

The first successful `kb_read_chunks` read or nonempty `tracker_lookup` establishes the conversation's fixed
relationship origin (a child document maps to its top-level KB). Candidate discovery, schema previews and empty
queries do not claim it. The origin is stored on the conversation by migration `0047_conversation_relationship_origin`
and survives follow-up turns, restarts and changes to the default search KB. Existing conversations reuse their
earliest resolvable published source. A new conversation gets its own origin.

Only that origin may attach `kb_relationships` navigation metadata. Derived KBs return their content or table
results without their own relationships, and explicitly requesting `kb_relationships` for a derived KB also
returns no links. Thus A may lead to B, but B does not reveal C. This restriction controls relationship expansion;
ordinary searches of permitted KBs remain available. Permissions and any explicit equipment/KB scope still apply.
If the origin is deleted or inaccessible, relationship expansion stops rather than claiming a different origin.

Automatic hints include up to four edges and at most 2,000 characters, within the existing tool response and
turn budgets. Requested facts take priority: near a response limit the tool can return a small notice directing
the agent to `kb_relationships(kb_slug, offset)` for details instead of discarding a chunk or table cell.
That tool defaults to the conversation's fixed origin and pages up to eight edges within 4,000 characters, with `next_offset`/`has_more`, and includes full
1,000-character summaries when they fit. Hidden endpoints do not appear in hints or pagination counts.

The agent treats user relationship descriptions as routing hints, not factual evidence or instructions.
An uncommented edge supplies no join keys or business semantics; those must be verified from actual data.
It decides whether another KB helps the question. Related manual content follows the same process:
`kb_search(kb_slug=target)` returns candidate document/version metadata and short previews;
`kb_read_chunks` loads chosen candidates; `kb_select_evidence` selects all actually used chunks across KBs.
Related business data uses `tracker_preview` followed by a filtered/aggregated `tracker_lookup`.
Existing retrieval limits and repeated-call protection remain in force.

Navigation metadata is excluded from evidence snapshots and final citations. Editing a relationship alone
does not invalidate a previously verified tracking-table result. Only retrieved and selected source material
and verified table queries can support the final answer.

Validation: `manage.py test kb.tests.test_kb_links kb.tests.test_rag_selection kb.tests.test_tracker_query
kb.tests.test_equipment_scope`; `node --test scripts/tests/kb_connections.test.cjs`.
