export interface Credits {
  plan: string;
  daily_limit: number;
  used: number;
  remaining: number;
  unlimited: boolean;
  request_status: string | null;
}

export interface Referral {
  code: string;
  referral_count: number;
  bonus_daily_tokens: number;
}
