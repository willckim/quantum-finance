# SampleCo Finance -- public load balancer for the synthetic AP portal
resource "aws_lb_listener" "ap_portal_https" {
  port       = 443
  protocol   = "HTTPS"
  ssl_policy = "ELBSecurityPolicy-TLS-1-0-2015-04"
}

resource "aws_lb_listener" "vendor_portal_https" {
  port       = 443
  protocol   = "HTTPS"
  ssl_policy = "ELBSecurityPolicy-2016-08"
}
