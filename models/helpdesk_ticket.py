import datetime
import pytz
from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError

# Model Baru untuk Master SLA
class HelpdeskMasterSLA(models.Model):
    _name = "helpdesk.master.sla"
    _description = "Master SLA Helpdesk"
    _order = "sequence, sla_hour"

    name = fields.Char(string="Level Case", required=True, help="Contoh: Easy, Medium, Hard, Urgent")
    sequence = fields.Integer(string="Sequence", default=10)
    sla_hour = fields.Float(string="SLA Target (Jam)", required=True, default=8.0)
    active = fields.Boolean(default=True)


class HelpdeskTicket(models.Model):
    _name = "helpdesk.ticket"
    _description = "Helpdesk Ticket"
    _rec_name = "number"
    _order = "priority desc, sequence, number desc, id desc"
    _mail_post_access = "read"
    _inherit = ["mail.thread.cc", "mail.activity.mixin", "portal.mixin"]

    @api.model
    def _lang_get(self):
        return self.env["res.lang"].get_installed()

    def _get_default_stage_id(self):
        return self.env["helpdesk.ticket.stage"].search([], limit=1).id

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        stage_ids = self.env["helpdesk.ticket.stage"].search([])
        return stage_ids

    number = fields.Char(string="Ticket number", default="/", readonly=True)
    name = fields.Char(string="Title", required=True)
    description = fields.Html(required=True, sanitize_style=True)
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assigned user",
        tracking=True,
        index=True,
        domain="team_id and [('share', '=', False),('id', 'in', user_ids)] or [('share', '=', False)]",  # noqa: B950
    )
    user_ids = fields.Many2many(
        comodel_name="res.users", related="team_id.user_ids", string="Users"
    )
    stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Stage",
        group_expand="_read_group_stage_ids",
        default=_get_default_stage_id,
        tracking=True,
        ondelete="restrict",
        index=True,
        copy=False,
    )
    partner_id = fields.Many2one(comodel_name="res.partner", string="Contact")
    partner_name = fields.Char()
    partner_email = fields.Char(string="Email")
    partner_lang = fields.Selection(_lang_get, "Language")

    last_stage_update = fields.Datetime(
        string="Last Stage Update", default=fields.Datetime.now
    )
    assigned_date = fields.Datetime(string="Assigned Date")
    closed_date = fields.Datetime(string="Closed Date")
    closed = fields.Boolean(related="stage_id.closed")
    unattended = fields.Boolean(related="stage_id.unattended", store=True)
    tag_ids = fields.Many2many(comodel_name="helpdesk.ticket.tag", string="Tags")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    channel_id = fields.Many2one(
        comodel_name="helpdesk.ticket.channel",
        string="Channel",
        help="Channel indicates where the source of a ticket"
        "comes from (it could be a phone call, an email...)",
    )
    category_id = fields.Many2one(
        comodel_name="helpdesk.ticket.category",
        string="Category",
    )
    team_id = fields.Many2one(
        comodel_name="helpdesk.ticket.team",
        string="Team",
    )
    priority = fields.Selection(
        selection=[
            ("0", "Low"),
            ("1", "Medium"),
            ("2", "High"),
            ("3", "Very High"),
        ],
        string="Priority",
        default="1",
    )
    attachment_ids = fields.One2many(
        comodel_name="ir.attachment",
        inverse_name="res_id",
        domain=[("res_model", "=", "helpdesk.ticket")],
        string="Media Attachments",
    )
    color = fields.Integer(string="Color Index")
    kanban_state = fields.Selection(
        selection=[
            ("normal", "Default"),
            ("done", "Ready for next stage"),
            ("blocked", "Blocked"),
        ],
        string="Kanban State",
    )
    sequence = fields.Integer(
        string="Sequence",
        index=True,
        default=10,
        help="Gives the sequence order when displaying a list of tickets.",
    )
    active = fields.Boolean(default=True)

    # ---------------------------------------------------
    # CUSTOM FIELDS: HELPDESK MONITORING
    # ---------------------------------------------------
    
    # Indikator Helpdesk Team
    is_helpdesk_team = fields.Boolean(
        string='Is Helpdesk Team',
        compute='_compute_is_helpdesk_team'
    )
    
    # Relasi Karyawan (Auto-fill)
    requested_by_id = fields.Many2one(
        comodel_name='hr.employee',
        string='Requested By',
        default=lambda self: self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1),
        tracking=True
    )
    
    # Auto-tarik Departemen
    department_name = fields.Char(
        string="Department", 
        related="requested_by_id.department_id.name",
        store=True,
        tracking=True
    )

    # Menghubungkan Level Case langsung ke Master SLA (Perbaikan nama ke level_case_id)
    level_case_id = fields.Many2one(
        comodel_name="helpdesk.master.sla",
        string="Level Case",
        tracking=True,
    )
    
    solution = fields.Html(string="Solution", tracking=True)
    
    # SLA & Time Tracking
    sla_hour = fields.Float(
        string="SLA (hour)", 
        compute="_compute_sla_hour", 
        store=True,
        readonly=True,
        tracking=True
    )
    actual_hour = fields.Float(
        string="Actual (hour)", 
        compute="_compute_actual_hour", 
        store=True
    )
    sla_status = fields.Char(
        string="SLA ACHV", 
        compute="_compute_sla_status", 
        store=True
    )
    # ---------------------------------------------------

    def name_get(self):
        res = []
        for rec in self:
            res.append((rec.id, rec.number + " - " + rec.name))
        return res

    def assign_to_me(self):
        self.write({"user_id": self.env.user.id})

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id:
            self.partner_name = self.partner_id.name
            self.partner_email = self.partner_id.email
            self.partner_lang = self.partner_id.lang

    def _get_default_email_channel(self):
        return self.env.ref(
            "helpdesk_mgmt.helpdesk_ticket_channel_email",
            raise_if_not_found=False,
        )

    # ---------------------------------------------------
    # CRUD
    # ---------------------------------------------------

    def _creation_subtype(self):
        return self.env.ref("helpdesk_mgmt.hlp_tck_created")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("number", "/") == "/":
                vals["number"] = self._prepare_ticket_number(vals)
            if not vals.get("team_id") and vals.get("category_id"):
                vals["team_id"] = self._prepare_team_id(vals)
            if vals.get("user_id") and not vals.get("assigned_date"):
                vals["assigned_date"] = fields.Datetime.now()
            if self.env.context.get("fetchmail_cron_running") and not vals.get(
                "channel_id"
            ):
                channel_email_id = self._get_default_email_channel()
                if channel_email_id:
                    vals["channel_id"] = channel_email_id.id
        return super().create(vals_list)

    def copy(self, default=None):
        self.ensure_one()
        if default is None:
            default = {}
        if "number" not in default:
            default["number"] = self._prepare_ticket_number(default)
        res = super().copy(default)
        return res

    def write(self, vals):
        for _ticket in self:
            now = fields.Datetime.now()
            if vals.get("stage_id"):
                stage = self.env["helpdesk.ticket.stage"].browse([vals["stage_id"]])
                vals["last_stage_update"] = now
                if stage.closed:
                    vals["closed_date"] = now
            if vals.get("user_id"):
                vals["assigned_date"] = now
        return super().write(vals)

    def action_duplicate_tickets(self):
        for ticket in self.browse(self.env.context["active_ids"]):
            ticket.copy()

    def _prepare_ticket_number(self, values):
        seq = self.env["ir.sequence"]
        if "company_id" in values:
            seq = seq.with_company(values["company_id"])
        return seq.next_by_code("helpdesk.ticket.sequence") or "/"

    def _compute_access_url(self):
        super()._compute_access_url()
        for item in self:
            item.access_url = "/my/ticket/%s" % (item.id)

    def _prepare_team_id(self, values):
        category = self.env["helpdesk.ticket.category"].browse(values["category_id"])
        if category.default_team_id:
            return category.default_team_id.id

    # ---------------------------------------------------
    # Mail gateway
    # ---------------------------------------------------

    def _track_template(self, tracking):
        res = super()._track_template(tracking)
        ticket = self[0]
        if "stage_id" in tracking and ticket.stage_id.mail_template_id:
            res["stage_id"] = (
                ticket.stage_id.mail_template_id,
                {
                    "auto_delete_message": True,
                    "subtype_id": self.env["ir.model.data"].xmlid_to_res_id(
                        "mail.mt_note"
                    ),
                    "email_layout_xmlid": "mail.mail_notification_light",
                },
            )
        return res

    @api.model
    def message_new(self, msg, custom_values=None):
        if custom_values is None:
            custom_values = {}
        partner_name, partner_email = next(
            iter(tools.email_split_tuples(msg.get("from"))), ("", "")
        )
        defaults = {
            "name": msg.get("subject") or _("No Subject"),
            "description": msg.get("body"),
            "partner_email": partner_email,
            "partner_id": msg.get("author_id"),
        }
        if not msg.get("author_id"):
            defaults["partner_name"] = partner_name
        defaults.update(custom_values)

        ticket = super().message_new(msg, custom_values=defaults)

        email_list = tools.email_split(
            (msg.get("to") or "") + "," + (msg.get("cc") or "")
        )
        partner_ids = [
            p.id
            for p in self.env["mail.thread"]._mail_find_partner_from_emails(
                email_list, records=ticket, force_create=False
            )
            if p
        ]
        ticket.message_subscribe(partner_ids)

        return ticket

    def message_update(self, msg, update_vals=None):
        email_list = tools.email_split(
            (msg.get("to") or "") + "," + (msg.get("cc") or "")
        )
        partner_ids = [
            p.id
            for p in self.env["mail.thread"]._mail_find_partner_from_emails(
                email_list, records=self, force_create=False
            )
            if p
        ]
        self.message_subscribe(partner_ids)
        return super().message_update(msg, update_vals=update_vals)

    def _message_get_suggested_recipients(self):
        recipients = super()._message_get_suggested_recipients()
        try:
            for ticket in self:
                if ticket.partner_id:
                    ticket._message_add_suggested_recipient(
                        recipients, partner=ticket.partner_id, reason=_("Customer")
                    )
        except AccessError:
            pass
        return recipients

    def _notify_get_reply_to(
        self, default=None, records=None, company=None, doc_names=None
    ):
        aliases = (
            self.sudo()
            .mapped("team_id")
            ._notify_get_reply_to(
                default=default, records=None, company=company, doc_names=None
            )
        )
        res = {ticket.id: aliases.get(ticket.team_id.id) for ticket in self}
        leftover = self.filtered(lambda rec: not rec.team_id)
        if leftover:
            res.update(
                super(HelpdeskTicket, leftover)._notify_get_reply_to(
                    default=default, records=None, company=company, doc_names=doc_names
                )
            )
        return res

    def action_send_email(self):
        if not self.partner_id:
            return self.env["ir.actions.actions"]._for_xml_id(
                "helpdesk_mgmt.action_create_select_partner"
            )
        else:
            return self.action_do_send_email()

    def action_do_send_email(self):
        self.ensure_one()
        self.partner_lang or self.env.context.get("lang")
        ctx = {
            "default_model": "helpdesk.ticket",
            "default_res_id": self.ids[0],
            "default_composition_mode": "comment",
            "default_partner_ids": [self.partner_id.id],
            "force_email": True,
        }
        return {
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "views": [(False, "form")],
            "view_id": False,
            "target": "new",
            "context": ctx,
        }

    # ---------------------------------------------------
    # FUNGSI LOGIKA PERHITUNGAN SLA & HELPDESK TEAM
    # ---------------------------------------------------
    @api.depends('company_id')
    @api.depends_context('uid')
    def _compute_is_helpdesk_team(self):
        """Cek akses Helpdesk Team"""
        for ticket in self:
            is_team = (
                self.env.user.has_group('helpdesk_mgmt.group_helpdesk_user') or 
                self.env.user.has_group('helpdesk_mgmt.group_helpdesk_manager') or
                self.env.user.has_group('helpdesk_mgmt.group_helpdesk_user_team')
            )
            ticket.is_helpdesk_team = is_team

    @api.depends('level_case_id')
    def _compute_sla_hour(self):
        """Target SLA otomatis ditarik dari Master SLA yang dipilih"""
        for ticket in self:
            ticket.sla_hour = ticket.level_case_id.sla_hour if ticket.level_case_id else 0.0

    @api.depends('create_date', 'closed_date')
    def _compute_actual_hour(self):
        """Jam kerja efektif (07:30-16:30 WIB, Sen-Jum)"""
        tz = pytz.timezone('Asia/Jakarta')
        
        for ticket in self:
            if ticket.create_date and ticket.closed_date:
                # UTC ke WIB
                start_utc = pytz.utc.localize(ticket.create_date)
                end_utc = pytz.utc.localize(ticket.closed_date)
                
                start_tz = start_utc.astimezone(tz)
                end_tz = end_utc.astimezone(tz)
                
                total_seconds = 0
                current_date = start_tz.date()
                end_date = end_tz.date()
                
                # Loop per hari
                while current_date <= end_date:
                    # Skip akhir pekan
                    if current_date.weekday() < 5:
                        # Set jam operasional
                        work_start = tz.localize(datetime.datetime.combine(current_date, datetime.time(7, 30)))
                        work_end = tz.localize(datetime.datetime.combine(current_date, datetime.time(16, 30)))
                        
                        # Hitung overlap waktu
                        overlap_start = max(start_tz, work_start)
                        overlap_end = min(end_tz, work_end)
                        
                        if overlap_start < overlap_end:
                            total_seconds += (overlap_end - overlap_start).total_seconds()
                    
                    # Next day
                    current_date += datetime.timedelta(days=1)
                
                # Detik ke jam
                ticket.actual_hour = total_seconds / 3600.0
            else:
                ticket.actual_hour = 0.0

    @api.depends('actual_hour', 'sla_hour', 'closed_date')
    def _compute_sla_status(self):
        """Status SLA ACHV"""
        for ticket in self:
            if ticket.closed_date:
                if ticket.actual_hour <= ticket.sla_hour:
                    ticket.sla_status = "CLOSED ON TIME"
                else:
                    ticket.sla_status = "LATE"
            else:
                ticket.sla_status = "OPEN"