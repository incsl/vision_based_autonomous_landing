import rospy
from mavros_msgs.msg import GlobalPositionTarget, State, PositionTarget, AttitudeTarget
from mavros_msgs.srv import CommandBool, CommandTOL, SetMode
from geometry_msgs.msg import PoseStamped, Twist, Vector3Stamped
from sensor_msgs.msg import Imu, NavSatFix
from std_msgs.msg import Float32, Float64, String,Float32MultiArray
import time
from pyquaternion import Quaternion
import math
import threading



class Px4Controller:

    def __init__(self):

        self.imu = None
        self.gps = None
        self.local_pose = None
        self.current_state = None
        self.current_heading = None
        self.local_enu_position = None

        self.cur_target_pose = None
        self.cur_target_ati = None
        self.global_target = None
        self.thrust=0.58

        self.received_new_task = False
        self.arm_state = False
        self.offboard_state = False
        self.received_imu = False
        self.frame = "BODY"

        self.Pos_target_x=0
        self.Pos_target_y=0
        self.Pos_target_z=6

        self.Ati_target_x=0
        self.Ati_target_y=0

        self.alti_err = 0
        self.pre_alti_err = 0
        self.alti_err_int = 0

        self.Pos_err_x = 0
        self.pr_Pos_err_x = 0
        self.Pos_err_int_x = 0
 
        self.Pos_err_y = 0
        self.pr_Pos_err_y = 0
        self.Pos_err_int_y = 0

        self.al_Kp = 0.035
        self.al_Ki = 0.000001
        self.al_Kd = 0.1

        self.cal_ati_x=0
        self.cal_ati_y=0

        self.state = None

        self.sta=0
        self.cn=0
        self.dt=0

        self.p_Kp = 0.6
        self.p_Ki = 0.000001
        self.p_Kd = 3

        self.detect=0

        self. pub_flag=True

        self.camera_x=0
        self.camera_y=0

        '''
        ros subscribers
        '''
        self.local_pose_sub = rospy.Subscriber("/mavros/local_position/pose", PoseStamped, self.local_pose_callback)
        self.mavros_sub = rospy.Subscriber("/mavros/state", State, self.mavros_state_callback)
        self.gps_sub = rospy.Subscriber("/mavros/global_position/global", NavSatFix, self.gps_callback)
        self.imu_sub = rospy.Subscriber("/mavros/imu/data", Imu, self.imu_callback)

        self.set_target_position_sub = rospy.Subscriber("gi/set_pose/position", PoseStamped, self.set_target_position_callback)
        self.set_target_yaw_sub = rospy.Subscriber("gi/set_pose/orientation", Float32, self.set_target_yaw_callback)
        self.camera_deection_pub_sub = rospy.Subscriber("camera/detection", Float32MultiArray, self.detection_callback)

        '''
        ros publishers
        '''
        self.attitude_target_pub = rospy.Publisher('mavros/setpoint_raw/attitude', AttitudeTarget, queue_size=10)

        '''
        ros services
        '''
        self.armService = rospy.ServiceProxy('/mavros/cmd/arming', CommandBool)
        self.flightModeService = rospy.ServiceProxy('/mavros/set_mode', SetMode)

        print("Px4 Controller Initialized!")

    def start(self):
        rospy.init_node("offboard_node")
	self.alti_err_int = 0
        self.cur_target_ati = self.construct_target_ati(0, 0, 0,self.thrust)
        #print ("self.cur_target_ati:", self.cur_target_ati, type(self.cur_target_ati))

        for i in range(10):
            self.attitude_target_pub.publish(self.cur_target_ati)

            self.arm_state = self.arm()
            self.offboard_state = self.offboard()
            time.sleep(0.2)

        '''
        main ROS thread
        '''
        minus=1

        while self.arm_state and self.offboard_state and (rospy.is_shutdown() is False):
            
            if self.detect is 0:
                minus=minus*-1
                #searching
                self.Pos_target_x = self.Pos_target_x +2
                time.sleep(1)
                self.Pos_target_y=self.Pos_target_y+6*minus
                time.sleep(10)
            
            if (self.state is "LAND") and (self.local_pose.pose.position.z<1.7):
                self.Pos_target_z=0.1
                self.state = "DISARMED"

            time.sleep(0.1)

    def construct_target_ati(self, x, y, z,thrust):
        target_raw_atti = AttitudeTarget()
        target_raw_atti.header.stamp = rospy.Time.now()

        target_raw_atti.body_rate.x = x*math.pi/180
        target_raw_atti.body_rate.y = y*math.pi/180
        target_raw_atti.body_rate.z = z*math.pi/180

        target_raw_atti.type_mask = AttitudeTarget.IGNORE_ATTITUDE 
        
        target_raw_atti.thrust = thrust

        return target_raw_atti


    def position_distance(self, cur_p, target_p, threshold=0.1):
        delta_x = math.fabs(cur_p.pose.position.x - target_p.position.x)
        delta_y = math.fabs(cur_p.pose.position.y - target_p.position.y)
        delta_z = math.fabs(cur_p.pose.position.z - target_p.position.z)

        if (delta_x + delta_y + delta_z < threshold):
            return True
        else:
            return False


    def local_pose_callback(self, msg):
        self.local_pose = msg
        self.local_enu_position = msg
        self.alti_con()


    def mavros_state_callback(self, msg):
        self.mavros_state = msg.mode


    def imu_callback(self, msg):
        global global_imu, current_heading
        self.imu = msg
        self.current_heading = self.q2yaw(self.imu.orientation)

        self.received_imu = True

    def gps_callback(self, msg):
        self.gps = msg

    def body2enu(self, body_target_x, body_target_y, body_target_z):

        ENU_x = body_target_y
        ENU_y = - body_target_x
        ENU_z = body_target_z

        return ENU_x, ENU_y, ENU_z


    def BodyOffsetENU2FLU(self, msg):

        FLU_x = msg.pose.position.x * math.cos(self.current_heading) - msg.pose.position.y * math.sin(self.current_heading)
        FLU_y = msg.pose.position.x * math.sin(self.current_heading) + msg.pose.position.y * math.cos(self.current_heading)
        FLU_z = msg.pose.position.z

        return FLU_x, FLU_y, FLU_z


    def set_target_position_callback(self, msg):
        print("Received New Position Task!")

        if msg.header.frame_id == 'base_link':
            '''
            BODY_OFFSET_ENU
            '''
            # For Body frame, we will use FLU (Forward, Left and Up)
            #           +Z     +X
            #            ^    ^
            #            |  /
            #            |/
            #  +Y <------body

            self.frame = "BODY"


            FLU_x, FLU_y, FLU_z = self.BodyOffsetENU2FLU(msg)

            self.Pos_target_x = FLU_x + self.local_pose.pose.position.x
            self.Pos_target_y = FLU_y + self.local_pose.pose.position.y
            self.Pos_target_z = FLU_z + self.local_pose.pose.position.z
        else:
            '''
            LOCAL_ENU
            '''
            # For world frame, we will use ENU (EAST, NORTH and UP)
            #     +Z     +Y
            #      ^    ^
            #      |  /
            #      |/
            #    world------> +X

            self.frame = "LOCAL_ENU"

            self.Pos_target_x,self.Pos_target_y,self.Pos_target_z = self.body2enu(msg.pose.position.x, msg.pose.position.y, msg.pose.position.z)


    def position_PID(self):
        
        self.pr_Pos_err_x = self.Pos_err_x
        self.Pos_err_x = self.Pos_target_x - self.local_pose.pose.position.x
        self.Pos_err_int_x = self.Pos_err_int_x +( self.pr_Pos_err_x+ self.Pos_err_x) * self.dt/2

        self.pr_Pos_err_y = self.Pos_err_y
        self.Pos_err_y = self.Pos_target_y - self.local_pose.pose.position.y
        self.Pos_err_int_y = self.Pos_err_int_y + (self.Pos_err_y +  self.pr_Pos_err_y) * self.dt /2


        cal_ati_x  = (self.p_Kp * self.Pos_err_x) + (self.p_Ki * self.Pos_err_int_x) + (self.p_Kd * (self.Pos_err_x - self.pr_Pos_err_x) / self.dt)
        cal_ati_y  = (self.p_Kp * self.Pos_err_y) + (self.p_Ki * self.Pos_err_int_y) + (self.p_Kd * (self.Pos_err_y - self.pr_Pos_err_y) / self.dt)

        self.Ati_target_x=-cal_ati_y
        self.Ati_target_y=cal_ati_x         
    
        if ((self.Ati_target_x > 40) or (self.Ati_target_x < -40)):
        
            self.Ati_target_x = 40 * (self.Ati_target_x / abs(self.Ati_target_x))
        

        if ((self.Ati_target_y > 40) or (self.Ati_target_y < -40)):
        
            self.Ati_target_y = 40 * (self.Ati_target_y / abs(self.Ati_target_y ))

    def alti_con(self):
        try:
            self.dt = self.get_dt()
        except:
            self.dt=0
        if self.dt<0.0001:
            return

        self.position_PID()

        self.pre_alti_err=self.alti_err
        self.alti_err=self.Pos_target_z-self.local_pose.pose.position.z
        self.alti_err_int =self.alti_err_int+ (self.pre_alti_err+self.alti_err) * self.dt/2

        self.thrust = 0.56 + (self.al_Kp * self.alti_err) + (self.al_Ki * self.alti_err_int) + (self.al_Kd * (self.alti_err - self.pre_alti_err) / self.dt)

        #print "P_err", self.Pos_err_x, self.Pos_err_y
        #print "Targ", self.Pos_target_x, self.Pos_target_y
        #print "Atti", self.Ati_target_x, self.Ati_target_y
        #print self.alti_err, self.thrust
        print self.Pos_err_int_x, self.Pos_err_int_y, self.alti_err_int, self.Pos_target_z

        #print self.local_pose.pose.position.x, self.local_pose.pose.position.y, self.local_pose.pose.position.z

        if self.thrust>1:
            self.thrust=0.999

        self.cur_target_ati  = self.construct_target_ati(self.Ati_target_x,
                                                         self.Ati_target_y,
                                                         0,
                                                         self.thrust)

        self.attitude_target_pub.publish(self.cur_target_ati)

    def get_dt(self):
        dt=rospy.get_time () -self.sta
        self.sta=rospy.get_time ()
        return dt

    def detection_callback(self,msg):
        self.detect=1
        x,y,z=msg.data

        self.camera_x=x
        self.camera_y=y

        self.Pos_target_x = self.local_pose.pose.position.x-y
        self.Pos_target_y = self.local_pose.pose.position.y-x
        if x <= 1 and y<=1 and x >= -1 and y>=-1:
            self.Pos_target_z = self.local_pose.pose.position.z-0.4
            self.state = "LAND"

        #print "Pos", self.local_pose.pose.position.x, self.local_pose.pose.position.y
        #print "Ter", self.Pos_target_x, self.Pos_target_y
        #print "Err", x,y

    def set_target_yaw_callback(self, msg):
        print("Received New Yaw Task!")

        yaw_deg = msg.data * math.pi / 180.0
        self.cur_target_pose = self.construct_target(self.local_pose.pose.position.z,
                                                     yaw_deg)

    '''
    return yaw from current IMU
    '''
    def q2yaw(self, q):
        if isinstance(q, Quaternion):
            rotate_z_rad = q.yaw_pitch_roll[0]
        else:
            q_ = Quaternion(q.w, q.x, q.y, q.z)
            rotate_z_rad = q_.yaw_pitch_roll[0]

        return rotate_z_rad


    def arm(self):
        if self.armService(True):
            return True
        else:
            print("Vehicle arming failed!")
            return False


    def offboard(self):
        if self.flightModeService(custom_mode='OFFBOARD'):
            return True
        else:
            print("Vechile Offboard failed")
            return False


if __name__ == '__main__':

    con = Px4Controller()
    con.start()


